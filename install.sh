#!/bin/bash
# invest 技能族安装器：monorepo(嵌套结构) → skills 目录(平铺真实目录)
# 用法: ./install.sh [目标skills目录]     默认 /Users/z/Documents/work/.claude/skills
# 更新: 重跑本脚本(rsync 增量; 运行数据 outputs/cache/watchlist.json 不被覆盖删除)
set -e

REPO="git@github.com:Ming-H/invest.git"
STAGE="$HOME/.claude/skill-repos/invest"
DEST="${1:-/Users/z/Documents/work/.claude/skills}"
SKILLS="tasks/earnings-reader tasks/a-share-review stack/engine/tenscreener \
stack/methodology/jingshui stack/methodology/can-slim stack/methodology/lynch \
stack/data/cninfo-disclosure"

# ① 暂存克隆 / 更新(嵌套结构完整保留在暂存区)
if [ -d "$STAGE/.git" ]; then
  git -C "$STAGE" pull -q && echo "· 暂存仓已更新: $STAGE"
else
  git clone -q "$REPO" "$STAGE" && echo "· 已克隆: $REPO -> $STAGE"
fi

# ② 平铺安装(rsync 增量; --delete 只清代码层过期文件, 排除项在目标侧受保护)
#    v4 仓库嵌套(tasks/stack), 目标按 basename 平铺(Claude Code 只认 skills/<名>/SKILL.md)
mkdir -p "$DEST"
for s in $SKILLS; do
  name="$(basename "$s")"
  rsync -a --delete \
    --exclude '.git' --exclude 'outputs' --exclude 'cache' \
    --exclude 'watchlist.json' --exclude '__pycache__' --exclude '.DS_Store' \
    "$STAGE/$s/" "$DEST/$name/"
  [ -f "$DEST/$name/SKILL.md" ] && echo "✓ $name" || echo "⚠ $name 缺 SKILL.md"
done

echo "完成: $(echo $SKILLS | wc -w | tr -d ' ') 个技能 -> $DEST"
