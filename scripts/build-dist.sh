#!/usr/bin/env bash
# 打包可分发产物：dist/neo-skill-<version>.zip、.tar.gz 与各自的 .sha256。
#
# 设计要点（可重现构建）：
#   1. 顶层目录固定为 neo-skill-<version>/，版本号从 runtime 里的 __version__ 读，不猜；
#   2. 遍历按相对路径排序，两次构建的条目顺序完全一致；
#   3. 所有条目的 mtime 固定为同一个时间戳（SOURCE_DATE_EPOCH，默认 2025-10-01T00:00:00Z）；
#   4. 属主归一：uid/gid = 0，uname/gname 为空字符串；
#   5. 权限位固定：可执行文件 0755，其余 0644；zip 用 external_attr 高 16 位承载 st_mode，
#      这样 unzip 出来 `flow-core` 仍是 -rwxr-xr-x，可直接 `./flow-core --version`；
#   6. 排除 __pycache__ / *.pyc / .pytest_cache / .git / .codegraph / dist 等非产物垃圾；
#   7. tar.gz 的 gzip 头 mtime 归零、不写文件名，避免压缩器把时间与路径带进产物。
#
# 用法：
#   scripts/build-dist.sh                 # 产物写到 <repo>/dist/
#   scripts/build-dist.sh --out DIR       # 自定义输出目录
#   scripts/build-dist.sh --check         # 连续构建两次并比较字节与 sha256（可重现自检）
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR=""
CHECK=0

while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT_DIR="${2:?--out 需要一个目录}"; shift 2 ;;
    --check) CHECK=1; shift ;;
    -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

OUT_DIR="${OUT_DIR:-$REPO_ROOT/dist}"
mkdir -p "$OUT_DIR"

build_once() {
  local out_dir="$1"
  python3 - "$REPO_ROOT" "$out_dir" <<'PY'
import gzip
import hashlib
import io
import os
import stat
import sys
import tarfile
import zipfile

repo_root = os.path.abspath(sys.argv[1])
out_dir = os.path.abspath(sys.argv[2])

# --- 版本号：唯一事实来源是 flow_core/__init__.py 的 __version__ -------------
version = None
init_py = os.path.join(repo_root, "skills", "neo-skill", "runtime", "flow_core", "__init__.py")
with open(init_py, encoding="utf-8") as fh:
    for line in fh:
        if line.startswith("__version__"):
            version = line.split("=", 1)[1].strip().strip("\"'")
            break
if not version:
    sys.exit("无法从 %s 读取 __version__" % init_py)

top = "neo-skill-%s" % version
stamp = int(os.environ.get("SOURCE_DATE_EPOCH", "1759276800"))  # 2025-10-01T00:00:00Z

# --- 收集条目：只打随包资源，不打开发机痕迹 ---------------------------------
INCLUDE_TOP = ["skills", "README.md", "pyproject.toml"]
EXCLUDE_DIRS = {".git", ".codegraph", "__pycache__", ".pytest_cache", ".mypy_cache",
                ".ruff_cache", "dist", "build", ".venv", "venv", ".pi", "tmptest"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".pyd", ".so.orig", ".orig", ".rej")

entries = []  # [(相对 top 的路径, 绝对路径, 是否可执行)]


def add(path, rel):
    st = os.stat(path)
    entries.append((rel, path, bool(st.st_mode & stat.S_IXUSR)))


for name in INCLUDE_TOP:
    abs_path = os.path.join(repo_root, name)
    if not os.path.exists(abs_path):
        continue
    if os.path.isfile(abs_path):
        add(abs_path, name)
        continue
    for dirpath, dirnames, filenames in os.walk(abs_path):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for fn in sorted(filenames):
            if fn in EXCLUDE_DIRS or fn.endswith(EXCLUDE_SUFFIX):
                continue
            full = os.path.join(dirpath, fn)
            add(full, os.path.relpath(full, repo_root).replace(os.sep, "/"))

# 排序遍历：让两次构建的条目顺序完全一致
entries.sort(key=lambda e: e[0])


def mode_of(executable):
    return 0o755 if executable else 0o644


zip_path = os.path.join(out_dir, "%s.zip" % top)
tar_path = os.path.join(out_dir, "%s.tar.gz" % top)

# --- zip：用 external_attr 保权限位 ----------------------------------------
date_time = (2025, 10, 1, 0, 0, 0)
with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    for rel, full, executable in entries:
        info = zipfile.ZipInfo(filename="%s/%s" % (top, rel), date_time=date_time)
        info.create_system = 3  # unix，unzip 才会按 unix 权限位还原
        info.external_attr = ((mode_of(executable) | stat.S_IFREG) & 0xFFFF) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        with open(full, "rb") as fh:
            zf.writestr(info, fh.read())

# --- tar.gz：固定 mtime、归一属主、归零 gzip 头 ----------------------------
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
    for rel, full, executable in entries:
        info = tarfile.TarInfo(name="%s/%s" % (top, rel))
        data = open(full, "rb").read()
        info.size = len(data)
        info.mtime = stamp
        info.mode = mode_of(executable)
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        info.type = tarfile.REGTYPE
        tf.addfile(info, io.BytesIO(data))
with open(tar_path, "wb") as fh:
    # mtime=0 + 不写文件名 → 同一输入得同一字节
    with gzip.GzipFile(fileobj=fh, mode="wb", compresslevel=9, mtime=0) as gz:
        gz.write(buf.getvalue())

for path in (zip_path, tar_path):
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    with open(path + ".sha256", "w", encoding="utf-8") as fh:
        fh.write("%s  %s\n" % (digest, os.path.basename(path)))
    print("%s  %s" % (digest, os.path.relpath(path, repo_root)))
PY
}

if [ "$CHECK" = "1" ]; then
  tmp_a="$(mktemp -d)"; tmp_b="$(mktemp -d)"
  trap 'rm -rf "$tmp_a" "$tmp_b"' EXIT
  build_once "$tmp_a" >/dev/null
  sleep 1  # 时间流逝不应影响产物；这行本身也是断言的一部分
  build_once "$tmp_b" >/dev/null
  if ! diff -r "$tmp_a" "$tmp_b" >/dev/null; then
    echo "可重现性自检失败：两次构建产物不同" >&2
    diff -r "$tmp_a" "$tmp_b" >&2 || true
    exit 1
  fi
  echo "可重现性自检通过：两次构建字节一致（$(ls -1 "$tmp_a" | tr '\n' ' ')）"
  exit 0
fi

build_once "$OUT_DIR"
echo "产物目录：$OUT_DIR"
