#!/usr/bin/env pwsh
<#
.SYNOPSIS
    启动整个可靠电商多智能体平台 —— dev.sh 的 PowerShell 版本。

.DESCRIPTION
    行为与 scripts/dev.sh 完全一致：相同的 profile、相同的执行顺序、
    相同的健康检查、相同的参数。它存在的理由是 dev.sh 是 bash 脚本，
    无法在 PowerShell 或 cmd 中运行。

    但它并不只适用于 Windows：PowerShell 7 是跨平台的，因此本脚本在
    macOS 与 Linux 上同样可用；在这些平台上 dev.sh 更符合习惯，
    两者可以互换使用。

    这里的每一步都只是普通的 `docker compose` 调用。如果本脚本出现异常，
    docs/quick-start.md 中的命令可以手工完成同样的工作。

.PARAMETER Clean
    删除容器与数据卷，然后不带缓存重新构建。

.PARAMETER SeedOnly
    对已有数据库重跑种子数据后退出。

.PARAMETER InfraOnly
    只启动 db + redis + jaeger，不启动智能体与前端。

.PARAMETER Demo
    从 GHCR 拉取预构建镜像，不进行本地构建。这是最快路径：
    无需本地构建，一条命令。可用 $env:IMAGE_TAG 指定标签（默认 latest）。

.EXAMPLE
    ./scripts/dev.ps1
    ./scripts/dev.ps1 -Clean
    ./scripts/dev.ps1 -Demo
#>
[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$SeedOnly,
    [switch]$InfraOnly,
    [switch]$Demo
)

# 遇到第一个未处理错误即停止。原生命令本身不会触发该行为，
# 因此下面通过 Invoke-Compose 显式检查退出码。
$ErrorActionPreference = 'Stop'

function Write-Step    { param([string]$Message) Write-Host "`n── $Message ──`n" -ForegroundColor Cyan }
function Write-Info    { param([string]$Message) Write-Host "[信息]  $Message" -ForegroundColor Blue }
function Write-Ok      { param([string]$Message) Write-Host "[完成]  $Message" -ForegroundColor Green }
function Write-Warn    { param([string]$Message) Write-Host "[警告]  $Message" -ForegroundColor Yellow }
function Write-Err     { param([string]$Message) Write-Host "[错误]  $Message" -ForegroundColor Red }

# ── 技术栈参数 ───────────────────────────────────────────────
# 与 dev.sh 的 COMPOSE / APP_PROFILES / RUN_PROFILES 保持一致。
# docker-compose.yml 把 agents、seeder、frontend 放在 profile 之后，
# 只有 db、redis、jaeger 是无条件启动的。
#   AppProfiles —— down / build：全部受 profile 控制的服务（含 seed）
#   RunProfiles —— 最终 `up -d`：agents + frontend，排除 seed，
#                  因为种子数据已在前面的一次性 `run --rm` 中执行过

$ComposeArgs        = @('compose')
$AppProfiles        = @('--profile', 'seed', '--profile', 'agents', '--profile', 'frontend')
$RunProfiles        = @('--profile', 'agents', '--profile', 'frontend')
$PgDataVolumeRegex  = '_pgdata$'

function Invoke-Compose {
    <#
        执行 `docker compose ...`，非零退出码时抛错，除非指定 -AllowFailure。
        PowerShell 不会因为原生命令的退出码而中断管道，若不做此检查，
        构建失败后会静默继续执行下一步。
    #>
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [switch]$AllowFailure,
        [switch]$Quiet
    )
    $all = $ComposeArgs + $Arguments
    if ($Quiet) {
        & docker @all *> $null
    } else {
        & docker @all
    }
    if ($LASTEXITCODE -ne 0 -and -not $AllowFailure) {
        throw "docker $($all -join ' ') 执行失败，退出码 $LASTEXITCODE"
    }
    # 刻意不返回任何值。这里若有未捕获的返回值，会被写入成功流，
    # 在步骤之间打印出一个孤立的退出码。
}

function Wait-ForHealth {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string[]]$Probe,
        [int]$TimeoutSeconds = 60
    )
    Write-Info "等待 $Name 就绪..."
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        & docker @($ComposeArgs + $Probe) *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "$Name 已就绪"
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "$Name 在 ${TimeoutSeconds} 秒内未就绪"
}

function Wait-ForHttp {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Url,
        [int]$TimeoutSeconds = 60
    )
    Write-Info "等待 $Name 就绪..."
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            # -UseBasicParsing 让本脚本在 Windows PowerShell 5.1 上也能工作，
            # 否则 Invoke-WebRequest 需要依赖 Internet Explorer 引擎。
            $null = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
            Write-Ok "$Name 已就绪"
            return
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    throw "$Name 在 ${TimeoutSeconds} 秒内未响应（$Url）"
}

function Repair-StaleDatabaseVolume {
    <#
        上一次运行遗留的 pgdata 数据卷会保留原有密码，容器能正常启动健康检查，
        但所有连接都会被拒绝。dev.sh 有同样的恢复逻辑；没有它，
        种子数据步骤会以鉴权错误失败，看起来像是配置问题。
    #>
    $probe = @('exec', '-T', 'db', 'sh', '-c',
               'PGPASSWORD=ecommerce_secret psql -h 127.0.0.1 -U ecommerce -d ecommerce_agents -c "SELECT 1"')
    & docker @($ComposeArgs + $probe) *> $null
    if ($LASTEXITCODE -eq 0) { return }

    Write-Warn '数据库鉴权失败 —— 检测到过期数据卷。正在重新初始化...'
    Invoke-Compose -Arguments @('stop', 'db') -AllowFailure -Quiet
    Invoke-Compose -Arguments @('rm', '-f', 'db') -AllowFailure -Quiet

    $volumes = (& docker volume ls -q) | Where-Object { $_ -match $PgDataVolumeRegex }
    foreach ($volume in $volumes) { & docker volume rm $volume *> $null }

    Invoke-Compose -Arguments @('up', '-d', 'db')
    Wait-ForHealth -Name 'PostgreSQL' -TimeoutSeconds 60 -Probe @(
        'exec', 'db', 'pg_isready', '-h', '127.0.0.1', '-U', 'ecommerce', '-d', 'ecommerce_agents')
    Write-Ok '数据库已用正确凭据重新初始化'
}

function Assert-DatabaseSchema {
    <#
        .SYNOPSIS
        校验表结构是否匹配，而不只是凭据是否正确。

        .DESCRIPTION
        数据卷可能鉴权完全正常，但表结构仍是上一次 init.sql 变更之前的版本。
        这是更常见的踩坑方式 —— 拉取新提交后重启同一个栈 ——
        而且比鉴权失败更难排查，因为它不报任何错误：检索只是静默返回空结果，
        智能体回复「没有找到相关商品」，读起来像是商品目录为空，而不是索引损坏。

        products.search_vector 是哨兵字段：它随全文检索功能一起引入，
        任何早于该版本的数据卷都缺少它。这里刻意硬编码 ——
        从 init.sql 动态推导看似更聪明，但会随文件变化而漂移。
    #>
    $query = "SELECT 1 FROM information_schema.columns WHERE table_name = 'products' AND column_name = 'search_vector'"
    $probe = @('exec', '-T', 'db', 'sh', '-c',
               "PGPASSWORD=ecommerce_secret psql -h 127.0.0.1 -U ecommerce -d ecommerce_agents -tAc `"$query`"")
    $result = & docker @($ComposeArgs + $probe) 2>$null
    if ($result -match '1') { return }

    Write-Host ''
    Write-Warn '数据库表结构已过期 —— 缺少 products.search_vector 字段。'
    Write-Host ''
    Write-Host '  你的 Postgres 数据卷早于全文检索功能。该栈仍会正常启动并显示健康，'
    Write-Host '  但每次商品检索都会静默返回空结果。为避免进入这种状态，已拒绝启动。'
    Write-Host ''
    Write-Host '  两种处理方式：'
    Write-Host ''
    Write-Host '    重建数据卷（会丢失本地数据，从零重新播种）：'
    Write-Host '      ./scripts/dev.ps1 -Clean'
    Write-Host ''
    Write-Host '    或就地更新表结构，保留现有数据：'
    Write-Host '      docker compose exec -T db psql -U ecommerce -d ecommerce_agents < docker/postgres/init.sql'
    Write-Host ''
    exit 1
}

function Show-Summary {
    Write-Host ''
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host '  可靠电商多智能体平台 — 服务已启动' -ForegroundColor Cyan
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  前端            http://localhost:3000'
    Write-Host '  编排器          http://localhost:8080'
    Write-Host '  Jaeger（追踪）  http://localhost:16686'
    Write-Host ''
    Write-Host '  登录账号        zhangwei@example.com / customer123'
    Write-Host ''
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host ''
}

# ── 无论从何处调用，都切换到仓库根目录 ──
$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot
try {
    # ── 前置依赖 ─────────────────────────────────────────────
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Err '未检测到 Docker，或 Docker 不在 PATH 中。'
        Write-Err '在 Windows 上请安装 Docker Desktop 并确保其正在运行。'
        exit 1
    }
    & docker compose version *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Err '需要 Docker Compose v2（即 `docker compose` 子命令，而非 `docker-compose`）。'
        exit 1
    }

    # ── .env ─────────────────────────────────────────────────
    if (-not (Test-Path '.env')) {
        if (Test-Path '.env.example') {
            Copy-Item '.env.example' '.env'
            Write-Warn '已从 .env.example 创建 .env —— 请填入 API 密钥，否则智能体不会回答。'
        } else {
            Write-Err '未找到 .env 或 .env.example。请先创建 .env 文件。'
            exit 1
        }
    }

    # ── 清理 ─────────────────────────────────────────────────
    if ($Clean) {
        Write-Step '清理中（删除容器、数据卷与孤儿容器）'
        Invoke-Compose -Arguments ($AppProfiles + @('down', '-v', '--remove-orphans')) -AllowFailure
        # Jaeger 默认使用内存存储，没有持久卷；显式删除容器以清空历史追踪数据。
        Invoke-Compose -Arguments @('rm', '-f', 'jaeger') -AllowFailure -Quiet
        Write-Ok '清理完成 —— 容器、数据卷与 Jaeger 追踪数据均已清除'
    }

    # ── 演示模式 ─────────────────────────────────────────────
    # 与 dev.sh 的 --demo 对应：带独立退出的自包含快速路径，
    # 而不是把条件分支穿插到构建 / 播种 / 启动流程里。
    # 这里没有构建步骤，docker-compose.demo.yml 不带 profile，
    # 且它的 depends_on: service_completed_successfully 让 compose 自己
    # 安排种子数据的顺序。
    if ($Demo) {
        $tag = if ($env:IMAGE_TAG) { $env:IMAGE_TAG } else { 'latest' }
        $env:IMAGE_TAG = $tag
        Write-Step "演示模式 —— 拉取预构建镜像（标签：$tag）"
        Write-Info '无需本地构建。镜像来自 ghcr.io/s1encisi/reliable-commerce-agents'

        & docker compose -f docker-compose.demo.yml pull
        if ($LASTEXITCODE -ne 0) {
            Write-Err '有一个或多个镜像拉取失败。'
            Write-Host ''
            Write-Host '  最可能的原因：'
            Write-Host '    - 该标签尚不存在。'
            Write-Host '    - 某个镜像包仍为私有。匿名拉取会返回看起来像网络问题的鉴权错误，'
            Write-Host '      详见 docs/releasing.md。'
            Write-Host ''
            Write-Host '  如需改为从源码构建，去掉 -Demo 参数即可：'
            Write-Host '    ./scripts/dev.ps1'
            exit 1
        }
        Write-Ok '镜像已拉取'

        Write-Step '启动服务'
        & docker compose -f docker-compose.demo.yml up -d
        if ($LASTEXITCODE -ne 0) { Write-Err '服务启动失败'; exit 1 }

        Wait-ForHttp -Name '编排器' -Url 'http://localhost:8080/health' -TimeoutSeconds 120
        Wait-ForHttp -Name '前端' -Url 'http://localhost:3000' -TimeoutSeconds 120

        Show-Summary
        Write-Host '  登录账号 zhangwei@example.com / customer123' -ForegroundColor White
        Write-Host ''
        exit 0
    }

    # ── 仅播种 ───────────────────────────────────────────────
    if ($SeedOnly) {
        Write-Step '执行种子数据'
        Invoke-Compose -Arguments @('up', '-d', 'db', 'redis', 'jaeger')
        Wait-ForHealth -Name 'PostgreSQL' -TimeoutSeconds 60 -Probe @(
            'exec', 'db', 'pg_isready', '-h', '127.0.0.1', '-U', 'ecommerce', '-d', 'ecommerce_agents')
        Wait-ForHealth -Name 'Redis' -Probe @('exec', 'redis', 'redis-cli', 'ping')
        Repair-StaleDatabaseVolume
        Assert-DatabaseSchema
        Invoke-Compose -Arguments @('--profile', 'seed', 'run', '--rm', 'seeder')
        Write-Ok '种子数据执行完成'
        exit 0
    }

    # ── 停止已有容器 ─────────────────────────────────────────
    Write-Step '停止已有容器'
    Invoke-Compose -Arguments ($AppProfiles + @('down', '--remove-orphans')) -AllowFailure -Quiet

    # ── 构建 ─────────────────────────────────────────────────
    Write-Step '构建镜像'
    if ($Clean) {
        Invoke-Compose -Arguments ($AppProfiles + @('build', '--no-cache'))
    } else {
        Invoke-Compose -Arguments ($AppProfiles + @('build'))
    }

    # ── 启动基础设施 ─────────────────────────────────────────
    Write-Step '启动基础设施（db、redis、jaeger）'
    Invoke-Compose -Arguments @('up', '-d', 'db', 'redis', 'jaeger')
    Wait-ForHealth -Name 'PostgreSQL' -TimeoutSeconds 60 -Probe @(
        'exec', 'db', 'pg_isready', '-h', '127.0.0.1', '-U', 'ecommerce', '-d', 'ecommerce_agents')
    Wait-ForHealth -Name 'Redis' -Probe @('exec', 'redis', 'redis-cli', 'ping')
    Repair-StaleDatabaseVolume
    Assert-DatabaseSchema
    Write-Ok '基础设施已就绪'

    # ── 种子数据 ─────────────────────────────────────────────
    Write-Step '执行数据库种子数据'
    Invoke-Compose -Arguments @('--profile', 'seed', 'run', '--rm', 'seeder')
    Write-Ok '数据库播种完成'

    if ($InfraOnly) {
        Show-Summary
        Write-Ok '仅基础设施模式 —— 未启动智能体'
        exit 0
    }

    # ── 智能体与前端 ─────────────────────────────────────────
    Write-Step '启动智能体与前端'
    Invoke-Compose -Arguments ($RunProfiles + @('up', '-d'))

    Wait-ForHttp -Name '编排器'       -Url 'http://localhost:8080/health'
    Wait-ForHttp -Name '商品发现'     -Url 'http://localhost:8081/health'
    Wait-ForHttp -Name '订单管理'     -Url 'http://localhost:8082/health'
    Wait-ForHttp -Name '定价与促销'   -Url 'http://localhost:8083/health'
    Wait-ForHttp -Name '评论情感分析' -Url 'http://localhost:8084/health'
    Wait-ForHttp -Name '库存与履约'   -Url 'http://localhost:8085/health'
    Write-Ok '全部智能体已启动'

    Wait-ForHttp -Name '前端' -Url 'http://localhost:3000' -TimeoutSeconds 90
    Write-Ok '前端已启动'

    Show-Summary
    Write-Ok '可靠电商多智能体平台已就绪！'
} catch {
    Write-Err $_.Exception.Message
    exit 1
} finally {
    Pop-Location
}
