"""可靠电商多智能体平台的 Pydantic 配置。

集中声明应用环境变量，调用方通过 settings 或 shared.factory 获取配置。

兼容别名：
- AZURE_OPENAI_KEY 与 AZURE_OPENAI_API_KEY 绑定同一字段。
- AZURE_OPENAI_DEPLOYMENT 与 AZURE_OPENAI_DEPLOYMENT_NAME 绑定同一字段。
同时设置时，仓库原有名称优先，兼容现有 .env 文件与 MAF 文档写法。
"""

import logging
import os
from pathlib import Path

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# 低于该长度的密钥不满足 HS256 要求，
# 也会涵盖 .env.example 的占位值；32 字节等于 256 位。
# 满足 HS256 的最小密钥长度要求。
_MIN_SECRET_BYTES = 32
_UNSAFE_SECRET_DEFAULTS = {
    "change-me-in-production",
    "change-me-generate-a-random-256-bit-key",
    "agent-internal-secret",
    "agent-internal-shared-secret",
    "dev-oauth-seed-change-me",
}

# 当前真正实现的输入注入检测提供方。
# azure_content_safety 是规划中的保留值，
# 详见 docs/security-guide.md 的可选集成说明。
# shared/guardrails/azure_shield.py 尚未实现，因此选择它时应立即失败，
# 不能静默退回正则检测。
_SUPPORTED_INJECTION_PROVIDERS = {"regex"}
_NOT_YET_IMPLEMENTED_INJECTION_PROVIDERS = {"azure_content_safety"}
_SUPPORTED_GROUNDING_MODES = {"off", "observe", "annotate", "enforce"}
_SUPPORTED_COST_BUDGET_MODES = {"off", "observe", "enforce"}
_SUPPORTED_OUTPUT_MODERATION_MODES = {"off", "observe", "enforce"}

# 按仓库根目录解析 .env，
# 使评测与种子脚本不受启动目录影响。Docker 镜像根目录没有该文件，
# 容器配置来自 Compose 的 environment 块，
# 因此文件缺失是允许的。
# 本文件位于 <repo>/agents/python/shared/，仓库根目录需向上三层。
# parents[2] 只到 <repo>/agents，
# 会让 Pydantic 错过实际 .env，
# 即使文件存在也静默使用默认值。


def _resolve_repo_root(config_path: Path) -> Path:
    """定位仓库根目录，并兼容 Docker 镜像内的扁平目录。

    本地文件向上三层可到仓库根；镜像中路径是 /app/shared/config.py，
    只有两级父目录，直接读取 parents[3] 会在导入时失败。此时退回
    直接父目录，允许 .env 不存在，由容器环境变量提供配置。
    """
    parents = config_path.resolve().parents
    return parents[3] if len(parents) > 3 else config_path.resolve().parent


_REPO_ROOT = _resolve_repo_root(Path(__file__))
_ENV_FILE = Path(os.environ.get("APP_ENV_FILE", str(_REPO_ROOT / ".env")))


class Settings(BaseSettings):
    # 数据库配置
    DATABASE_URL: str = "postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents"

    # Redis 配置
    REDIS_URL: str = "redis://localhost:6379"

    # 限流配置，见 shared/rate_limit.py。
    # Redis 由基础设施预先提供，限流模块是它的实际使用方。
    # 限流默认启用，
    # 与 GUARDRAILS_ENABLED 和 HITL_ENABLED 的默认安全策略一致。
    # 匿名店铺也可访问智能体聊天，
    # 缺少限流会暴露模型费用滥用入口，
    # 因此不能依赖客户端自觉限制请求。
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_MAX_REQUESTS: int = 30
    RATE_LIMIT_WINDOW_SECONDS: float = 60.0

    # 大语言模型配置
    # 提供方密钥仅通过部署环境注入，禁止写入版本控制。
    DEEPSEEK_API_KEY: str = Field(default="", repr=False)
    MOONSHOT_API_KEY: str = Field(default="", repr=False)
    TYPESAFE_API_KEY: str = Field(default="", repr=False)
    EMBEDDING_PROVIDER: str = "auto"
    CAMPAIGN_BUDGET_PATH: str = str(_REPO_ROOT / ".local" / "upgrade-20260924-budget.json")
    MODEL_PRICES_JSON: str = "{}"
    DECISION_ROUTING_MODE: str = "off"
    DECISION_MIN_PROBABILITY: float = 1.0
    DECISION_CONTEXT_ENABLED: bool = False
    JEV_TIMEOUT_SECONDS: float = 1.5
    CONTEXT_MAX_BYTES: int = 16000
    MAF_STREAM_QUEUE_SIZE: int = 128
    MAF_STREAM_QUEUE_BYTES: int = 1048576
    VERIFIED_OUTPUT_ONLY: bool = False
    MODEL_MAX_CALLS_PER_RUN: int = 24
    EVALUATION_MODE: bool = False
    LLM_PROVIDER: str = "openai"  # 可选提供方：openai、azure、replay。
    LLM_MODEL: str = "gpt-4.1"
    # 为 OpenAI 兼容提供方指定可选 base_url。
    # 可连接 GitHub Models、OpenRouter、vLLM、
    # LM Studio 或 Azure AI Foundry 的兼容端点，
    # 替代 api.openai.com；默认不覆盖，
    # 仅在 LLM_PROVIDER=openai 时生效。配置示例见
    # tutorials/00-setup/README.md。
    LLM_BASE_URL: str = ""
    # 回放配置见 shared/replay_client.py。RECORD=true 时，
    # 缺少夹具会通过 REPLAY_RECORD_PROVIDER 发起真实调用，
    # 保存结果而不直接抛错。
    REPLAY_RECORD_PROVIDER: str = "openai"  # 可选 openai 或 azure，仅 RECORD=true 时使用。
    REPLAY_FIXTURES_DIR: str = "tests/fixtures/replay"
    RECORD: bool = False
    # 所有智能体运行的采样温度默认较低，
    # 以减少相同问题的答案波动。提供方默认温度约为 1.0 时，
    # 同一查询可能得到不同结果，例如是否找到边界价格商品。
    # 需要更多样的措辞时可提高温度，但不保证确定性。
    LLM_TEMPERATURE: float = 0.2
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_API_KEY: str = ""

    AZURE_OPENAI_ENDPOINT: str = ""

    # 同时接受 AZURE_OPENAI_KEY 与 MAF 文档的
    # AZURE_OPENAI_API_KEY；两者都存在时，
    # Pydantic 优先使用列在前面的别名。
    AZURE_OPENAI_KEY: str = Field(
        default="",
        validation_alias=AliasChoices("AZURE_OPENAI_KEY", "AZURE_OPENAI_API_KEY"),
    )

    AZURE_OPENAI_DEPLOYMENT: str = Field(
        default="",
        validation_alias=AliasChoices("AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_DEPLOYMENT_NAME"),
    )

    AZURE_OPENAI_API_VERSION: str = "2025-03-01-preview"
    AZURE_EMBEDDING_DEPLOYMENT: str = ""

    # 身份认证
    JWT_SECRET: str = "change-me-in-production"
    AGENT_SHARED_SECRET: str = "agent-internal-secret"

    # 可选的自托管 OAuth2 授权服务器
    # AUTH_MODE=local 默认沿用 HS256 JWT 与共享密钥。
    # AUTH_MODE=oauth 将用户登录、A2A 与 MCP 认证
    # 交给 agents/python/auth_server/ 中的自托管服务，
    # 通过 JWKS 校验其 RS256 令牌，不依赖外部身份提供方。
    AUTH_MODE: str = "local"  # 可选模式：local、oauth。

    AUTH_SERVER_ISSUER: str = "http://localhost:8090"
    AUTH_SERVER_JWKS_URL: str = "http://localhost:8090/.well-known/jwks.json"
    AUTH_SERVER_TOKEN_URL: str = "http://localhost:8090/oauth/token"
    AUTH_ACCESS_TOKEN_TTL: int = 3600  # 单位：秒。
    AUTH_REFRESH_TOKEN_TTL: int = 604800  # 7 天。
    AUTH_JWKS_CACHE_TTL: int = 900  # 资源服务器的 JWKS 缓存。
    AUTH_RSA_KEY_SIZE: int = 2048  # 仅用于授权服务器。
    AUTH_SIGNING_KEY_ENCRYPTION_KEY: str = ""  # 用于静态私钥加密的可选密钥加密密钥。

    # 各服务的 OAuth 客户端身份，使用客户端凭据授权。
    OAUTH_CLIENT_ID: str = ""  # 为空时默认使用服务名称。
    OAUTH_CLIENT_SECRET: str = ""  # 生产环境显式覆盖；开发环境从 OAUTH_SEED_KEY 派生。
    OAUTH_SEED_KEY: str = "dev-oauth-seed-change-me"  # 开发环境共享种子，不可用于生产。

    AUTH_ORCH_AUDIENCE: str = "ecommerce-orchestrator"
    AUTH_AGENT_AUDIENCE: str = "ecommerce-agents"

    # 授权服务器自身的资源标识，
    # 仅用于可选的动态客户端注册端点。
    # 它对应含 client:register 范围令牌的 aud，
    # 与编排器、智能体和 MCP 的受众不同。
    AUTH_SERVER_AUDIENCE: str = "ecommerce-auth-server"

    # RFC 7591 动态客户端注册入口 POST /oauth/register，
    # 默认关闭；通常由 scripts/seed.py 初始化固定客户端。
    # 启用后，注册请求仍然必须提供
    # 含 client:register 范围的令牌，详见 auth_server/register.py。
    # 该开关仅决定是否开放入口，不豁免认证。
    AUTH_ALLOW_DYNAMIC_REGISTRATION: bool = False

    # MCP 资源服务器认证，与 MCP_ENABLED 独立。
    MCP_AUTH_ENABLED: bool = False
    MCP_PRODUCT_AUDIENCE: str = "mcp-product"
    MCP_INVENTORY_AUDIENCE: str = "mcp-inventory"
    MCP_PRODUCT_REQUIRED_SCOPE: str = "mcp:product"
    MCP_INVENTORY_REQUIRED_SCOPE: str = "mcp:inventory"
    MCP_PRODUCT_RESOURCE_URL: str = "http://localhost:9000/mcp"
    MCP_INVENTORY_RESOURCE_URL: str = "http://localhost:9001/mcp"

    # 智能体注册表：A2A 端点映射。
    AGENT_REGISTRY: str = "{}"

    # 遥测配置
    OTEL_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:4317"
    OTEL_SERVICE_NAME: str = "ecommerce"
    GENAI_CAPTURE_CONTENT: bool = False

    # Langfuse：可选的并行 OTel 接收端。
    # 启用时，将追踪同时发送到 Langfuse 与主 OTLP 接收端。
    # 需要配置 Langfuse 账号。
    LANGFUSE_ENABLED: bool = False
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    # 人工参与（HITL）审批队列
    # 启用后，cancel_order、process_refund、initiate_return、
    # modify_order、place_backorder 等敏感工具需要管理员审批。
    # 请求保存在 hitl_requests 表中，
    # 通过 /admin/approvals 页面处理。
    HITL_ENABLED: bool = True

    # MCP 集成，由可选开关控制。
    # 启用后，专业智能体连接 MCP 服务访问数据，
    # 不再直接调用 asyncpg；配置的 MCP 地址必须可达。
    # 两种数据访问方式应保持相同业务语义。
    MCP_ENABLED: bool = False
    MCP_PRODUCT_SERVER_URL: str = "http://localhost:9000/mcp"
    MCP_INVENTORY_SERVER_URL: str = "http://localhost:9001/mcp"

    # 通用配置
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # MAF 功能开关，均提供兼容默认值。
    # 这些开关用于逐项启用重构能力，
    # 默认保持原有行为。

    # 会话状态存储后端：
    # postgres：使用既有 PostgreSQL 表结构。
    # file：写入 MAF_SESSION_DIR。
    # memory：仅内存保存，适合测试。
    MAF_SESSION_BACKEND: str = "postgres"
    MAF_SESSION_DIR: str = "./.sessions"

    # 持久工作流的检查点存储后端。
    MAF_CHECKPOINT_BACKEND: str = "postgres"  # 可选 postgres、file、memory。
    MAF_CHECKPOINT_DIR: str = "./.checkpoints"

    # 退货与换货工作流暂停审批的金额阈值；现有数值沿用美元计价。
    # 超过阈值时进入 HITL 人工审批。
    RETURN_HITL_THRESHOLD: float = 500.0

    # 为 true 时，专业智能体间交接不产生中间用户事件，
    # 保持当前行为；为 false 时发送交接事件。
    # 用于界面交接轨迹与可观测性。
    HANDOFF_AUTONOMOUS_MODE: bool = True

    # 自主模式会在智能体未交接时追加继续提示，
    # 再执行一轮；框架默认上限为 50 轮。
    # 若智能体无法交接，过高上限会造成大量无效调用。
    # 历史调试中曾产生长达 23637 字符、耗时 100–200 秒的独白；
    # 三轮通常可覆盖交接、交回与收尾，
    # 超过该范围应检查是否陷入循环。
    HANDOFF_MAX_TURNS: int = 3

    # 请求未指定时使用的默认编排模式，见 orchestrator/modes/。
    # tool 由模型调用 call_specialist_agent，
    # 没有额外前提；handoff 需要配置 AGENT_REGISTRY。
    # 旧名称 MAF_HANDOFF_MODE 已不能表达当前多模式注册表，
    # 因此改用 ORCHESTRATION_MODE，
    # 但仍接受旧名称作为别名，
    # 兼容已有 .env 与 Compose 配置。
    ORCHESTRATION_MODE: str = Field(
        default="tool",
        validation_alias=AliasChoices("ORCHESTRATION_MODE", "MAF_HANDOFF_MODE"),
    )

    # 单条 SSE 流的最大墙钟时长。
    # 达到秒数上限后，聊天端点中止底层生成器，
    # 即使模型还在输出 token 也会停止，
    # 避免失控流长期占用服务资源。
    MAF_STREAM_TIMEOUT_SECONDS: float = 120.0

    # 单条流用于持久化消息的最大缓冲字节数。
    # 模型输出超过此限制时，
    # 截断内容并发送 [truncated] 标记，
    # 让用户看到中止原因而非无限等待。
    MAF_STREAM_MAX_BYTES: int = 10_000_000

    # 启用时，CI 重新生成工作流图，并在产物漂移时失败。
    WORKFLOW_VISUALIZATION_ON_BUILD: bool = False

    # 安全护栏
    # 输出净化、注入检测与角色校验的代码层总开关，
    # 默认启用。
    GUARDRAILS_ENABLED: bool = True

    # 工具结果重新进入模型前，消除其中的存储型
    # 或间接提示注入。
    GUARDRAILS_OUTPUT_SANITIZATION: bool = True

    # 默认观察模式：护栏失败只记录，输入注入检测只计数。
    # 先评估误报率，再结合 GUARDRAILS_BLOCK_ON_INJECTION
    # 决定是否关闭失败放行。
    GUARDRAILS_FAIL_OPEN: bool = True

    # 启用后，输入检测器会拒绝包含注入的运行，
    # 而非仅记录日志；此选项独立于 FAIL_OPEN，
    # 可在阻止注入的同时保持净化错误不抛出。
    GUARDRAILS_BLOCK_ON_INJECTION: bool = False

    # 启用后校验智能体间转发的 x-user-email 与 x-user-role，
    # 发现异常时拒绝请求，不仅记录日志。
    GUARDRAILS_STRICT_IDENTITY: bool = False

    # 输入注入检测目前仅实现零依赖的 regex 提供方。
    # azure_content_safety 为保留选项，尚未实现，选择时会校验失败。
    GUARDRAILS_INJECTION_PROVIDER: str = "regex"

    # 服务端事实核验
    # off：不挂载事实核验中间件。
    # observe：核验并记录计数，不向调用方附加报告。
    # annotate：核验并将报告写入 additional_properties["grounding"]。
    # enforce：附加报告，并移除不存在的卡片、修正价格或总额。
    # 修正针对最终响应文本；
    # 流式限制详见 shared/grounding/middleware.py：
    # 可以修正持久化和最终结果，
    # 无法撤回已经发送给浏览器的分块。
    GROUNDING_MODE: str = "annotate"

    # 单次运行费用预算
    # 费用估算由 shared/cost.py 统一提供。
    # 仅在评测报告中事后计算费用，
    # 无法约束运行期间持续调用工具
    # 和追加提示的智能体循环。这里的两个配置项
    # 由 CostBudgetMiddleware 在运行时读取，
    # 用于累计和约束费用。
    #
    # off：不挂载费用预算中间件。
    # observe：累计并记录费用，
    # 即使超过 COST_BUDGET_USD_PER_RUN 也不阻止。
    # enforce：累计费用，并在达到预算后
    # 拒绝开始下一次模型调用。
    COST_BUDGET_MODE: str = "observe"

    # 单次运行累计估算费用上限，单位为美元。默认 None，
    # 即使 enforce 模式也不设置金额上限，
    # 需要使用者显式配置。
    COST_BUDGET_USD_PER_RUN: float | None = None

    # 模型输出内容审核
    # OutputSanitizationMiddleware 净化不可信工具输出，
    # 用于消除其中隐藏的恶意指令。
    # 模型自己生成的内容还需要单独审核，
    # 覆盖自伤、暴力、仇恨、骚扰和色情等类别；
    # 两者处理的威胁来源不同。
    #
    # off：不挂载输出审核中间件。
    # observe：分类最终响应并记录命中类别，
    # 但不阻止输出；这是默认模式。
    # 分类器位于 shared/guardrails/moderation.py，
    # 使用少量本地高精度规则，不是训练模型。
    # 默认避免误报阻止合法回答，
    # 同时保留观察记录供调整规则。
    # enforce：执行同样分类，并将非流式路径中
    # 命中规则的响应替换为拒绝提示。
    # 流式响应只能标记，不能撤回。
    # 这与 GROUNDING_MODE=enforce 的限制相同：
    # 已发送到浏览器的分块无法收回。
    OUTPUT_MODERATION_MODE: str = "observe"

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    @model_validator(mode="after")
    def _validate_secrets(self) -> "Settings":
        """生产环境遇到弱密钥或默认密钥立即失败；开发环境记录警告。

        HS256 要求至少 256 位密钥，但 PyJWT 不会自动拒绝较短占位值。
        非开发环境必须拒绝 .env.example 中的默认值；开发环境也明确告警。
        """
        is_prod = self.ENVIRONMENT.lower() not in {"development", "dev", "test"}

        def _check(name: str, value: str) -> None:
            stripped = value.strip()
            too_short = len(stripped.encode("utf-8")) < _MIN_SECRET_BYTES
            is_default = stripped in _UNSAFE_SECRET_DEFAULTS
            if too_short or is_default:
                msg = (
                    f"{name} is unsafe ("
                    + ("placeholder default" if is_default else f"{len(stripped)} chars < {_MIN_SECRET_BYTES}")
                    + "). Generate a fresh random 256-bit value."
                )
                if is_prod:
                    raise ValueError(msg)
                logger.warning("settings.secret_unsafe var=%s reason=%s", name, msg)

        _check("JWT_SECRET", self.JWT_SECRET)
        _check("AGENT_SHARED_SECRET", self.AGENT_SHARED_SECRET)

        if self.AUTH_MODE == "oauth":
            _check("OAUTH_SEED_KEY", self.OAUTH_SEED_KEY)
            if is_prod and not self.AUTH_SIGNING_KEY_ENCRYPTION_KEY.strip():
                msg = (
                    "AUTH_SIGNING_KEY_ENCRYPTION_KEY is required when AUTH_MODE=oauth "
                    "outside development — without it the auth-server stores its RSA "
                    "private key unencrypted."
                )
                raise ValueError(msg)

        return self

    @model_validator(mode="after")
    def _validate_injection_provider(self) -> "Settings":
        """拒绝尚未实现的注入检测提供方。

        不能接受 azure_content_safety 后静默运行 regex；应在启动阶段
        发现配置错误，避免生产流量使用与配置不同的检测机制。
        """
        provider = self.GUARDRAILS_INJECTION_PROVIDER
        if provider in _NOT_YET_IMPLEMENTED_INJECTION_PROVIDERS:
            raise ValueError(
                f"GUARDRAILS_INJECTION_PROVIDER={provider!r} is not implemented "
                f"(shared/guardrails/azure_shield.py does not exist yet). "
                f"Supported values: {sorted(_SUPPORTED_INJECTION_PROVIDERS)!r}."
            )
        if provider not in _SUPPORTED_INJECTION_PROVIDERS:
            raise ValueError(
                f"GUARDRAILS_INJECTION_PROVIDER={provider!r} is not a recognized value. "
                f"Supported values: {sorted(_SUPPORTED_INJECTION_PROVIDERS)!r}."
            )
        return self

    @model_validator(mode="after")
    def _validate_grounding_mode(self) -> "Settings":
        if self.GROUNDING_MODE not in _SUPPORTED_GROUNDING_MODES:
            raise ValueError(
                f"GROUNDING_MODE={self.GROUNDING_MODE!r} is not a recognized value. "
                f"Supported values: {sorted(_SUPPORTED_GROUNDING_MODES)!r}."
            )
        return self

    @model_validator(mode="after")
    def _validate_cost_budget_mode(self) -> "Settings":
        if self.COST_BUDGET_MODE not in _SUPPORTED_COST_BUDGET_MODES:
            raise ValueError(
                f"COST_BUDGET_MODE={self.COST_BUDGET_MODE!r} is not a recognized value. "
                f"Supported values: {sorted(_SUPPORTED_COST_BUDGET_MODES)!r}."
            )
        return self

    @model_validator(mode="after")
    def _validate_output_moderation_mode(self) -> "Settings":
        if self.OUTPUT_MODERATION_MODE not in _SUPPORTED_OUTPUT_MODERATION_MODES:
            raise ValueError(
                f"OUTPUT_MODERATION_MODE={self.OUTPUT_MODERATION_MODE!r} is not a recognized value. "
                f"Supported values: {sorted(_SUPPORTED_OUTPUT_MODERATION_MODES)!r}."
            )
        return self


settings = Settings()
