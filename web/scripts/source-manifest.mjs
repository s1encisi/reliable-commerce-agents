// 镜像构建时记录源码摘要，供最终运行镜像与工作区比对；不打包源码内容或环境文件。
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";

function files(directory) {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });
}
const paths = [...files("src"), "package.json", "pnpm-lock.yaml", "next.config.ts"].sort();
const manifest = Object.fromEntries(paths.map((path) => [path, createHash("sha256").update(readFileSync(path)).digest("hex")]));
writeFileSync("build-source-manifest.json", JSON.stringify(manifest, null, 2));
