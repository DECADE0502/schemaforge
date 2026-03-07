#!/usr/bin/env bash
#
# 固件平台改造 — 逐任务执行脚本（通过 happy 执行，手机可实时查看进度）
#
# 用法：
#   ./scripts/run-firmware-task.sh              # 自动找到下一个未完成的 task 并执行
#   ./scripts/run-firmware-task.sh 3            # 强制执行 Task 3
#   ./scripts/run-firmware-task.sh --status     # 查看所有 task 的完成状态
#   ./scripts/run-firmware-task.sh --log        # 查看 task 相关的 commit 历史
#   ./scripts/run-firmware-task.sh --revert N   # 回退 Task N 的 commit
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TASKS_FILE="$PROJECT_ROOT/docs/firmware-platform-tasks.md"
GUIDE_FILE="$PROJECT_ROOT/docs/firmware-platform-agent-guide.md"

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ──────────────────────────────────────────────
# happy 需要 Node >= 20，自动切换
# ──────────────────────────────────────────────
setup_node() {
    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    if [[ -s "$NVM_DIR/nvm.sh" ]]; then
        # shellcheck source=/dev/null
        . "$NVM_DIR/nvm.sh"
        nvm use 20 --silent 2>/dev/null || true
    fi
}

# ──────────────────────────────────────────────
# 解析某个 Task 的完成情况
# ──────────────────────────────────────────────
get_task_status() {
    local task_num=$1
    local in_task=0
    local total=0
    local checked=0

    while IFS= read -r line; do
        if [[ "$line" =~ ^##\ Task\ ${task_num}: ]]; then
            in_task=1
            continue
        fi
        if [[ $in_task -eq 1 ]] && [[ "$line" =~ ^##\  ]]; then
            break
        fi
        if [[ $in_task -eq 1 ]]; then
            if [[ "$line" =~ ^-\ \[x\] ]]; then
                ((total++))
                ((checked++))
            elif [[ "$line" =~ ^-\ \[\ \] ]]; then
                ((total++))
            fi
        fi
    done < "$TASKS_FILE"

    if [[ $total -eq 0 ]]; then
        echo "no-checkboxes"
    elif [[ $checked -eq $total ]]; then
        echo "done"
    elif [[ $checked -gt 0 ]]; then
        echo "partial"
    else
        echo "todo"
    fi
}

get_task_checked_count() {
    local task_num=$1
    local in_task=0
    local total=0
    local checked=0

    while IFS= read -r line; do
        if [[ "$line" =~ ^##\ Task\ ${task_num}: ]]; then
            in_task=1
            continue
        fi
        if [[ $in_task -eq 1 ]] && [[ "$line" =~ ^##\  ]]; then
            break
        fi
        if [[ $in_task -eq 1 ]]; then
            if [[ "$line" =~ ^-\ \[x\] ]]; then
                ((total++))
                ((checked++))
            elif [[ "$line" =~ ^-\ \[\ \] ]]; then
                ((total++))
            fi
        fi
    done < "$TASKS_FILE"

    echo "${checked}/${total}"
}

# ──────────────────────────────────────────────
# 获取 Task 标题
# ──────────────────────────────────────────────
get_task_title() {
    local task_num=$1
    grep -m1 "^## Task ${task_num}:" "$TASKS_FILE" | sed 's/^## //'
}

# ──────────────────────────────────────────────
# 显示所有 task 状态
# ──────────────────────────────────────────────
show_status() {
    echo ""
    echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}${BOLD}  固件平台改造 — 任务状态${NC}"
    echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════════${NC}"
    echo ""

    # 从 tasks 文件中动态提取所有 Task 编号（支持 7.1 等子任务）
    local task_nums
    task_nums=$(grep -oP '^## Task \K[0-9]+(\.[0-9]+)?' "$TASKS_FILE" | sort -t. -k1,1n -k2,2n)

    for i in $task_nums; do
        local status
        status=$(get_task_status "$i")
        local title
        title=$(get_task_title "$i")
        local count
        count=$(get_task_checked_count "$i")

        if [[ -z "$title" ]]; then
            continue
        fi

        case "$status" in
            done)    echo -e "  ${GREEN}[done]    $title${NC}  ($count)" ;;
            partial) echo -e "  ${YELLOW}[wip]     $title${NC}  ($count)" ;;
            todo)    echo -e "  ${RED}[todo]    $title${NC}  ($count)" ;;
            *)       echo -e "  [?]       $title" ;;
        esac
    done

    echo ""

    # 显示相关 commit 数量
    local commit_count
    commit_count=$(cd "$PROJECT_ROOT" && git log --oneline --grep="feat(firmware-platform)" 2>/dev/null | wc -l)
    if [[ $commit_count -gt 0 ]]; then
        echo -e "  ${CYAN}Git commits: ${commit_count} 个 task commit（--log 查看详情）${NC}"
        echo ""
    fi
}

# ──────────────────────────────────────────────
# 显示 task 相关 commit 历史
# ──────────────────────────────────────────────
show_log() {
    echo ""
    echo -e "${CYAN}${BOLD}  固件平台改造 — Commit 历史${NC}"
    echo ""

    cd "$PROJECT_ROOT"
    local commits
    commits=$(git log --oneline --grep="feat(firmware-platform)" 2>/dev/null || true)

    if [[ -z "$commits" ]]; then
        echo -e "  ${YELLOW}暂无 task commit${NC}"
    else
        echo "$commits" | while IFS= read -r line; do
            echo -e "  $line"
        done
    fi
    echo ""
}

# ──────────────────────────────────────────────
# 回退某个 Task 的 commit
# ──────────────────────────────────────────────
revert_task() {
    local task_num=$1
    cd "$PROJECT_ROOT"

    echo ""
    echo -e "${CYAN}查找 Task $task_num 的 commit ...${NC}"
    echo ""

    local commits
    commits=$(git log --oneline --grep="feat(firmware-platform): Task ${task_num} " 2>/dev/null || true)

    if [[ -z "$commits" ]]; then
        echo -e "${RED}未找到 Task $task_num 的 commit${NC}"
        exit 1
    fi

    echo "找到以下 commit："
    echo "$commits"
    echo ""
    echo -e "${YELLOW}选择回退方式：${NC}"
    echo "  1) git revert（安全，创建一个反向 commit）"
    echo "  2) git reset --hard（危险，彻底丢弃该 commit 之后的所有变更）"
    echo "  3) 取消"
    echo ""
    read -rp "选择 [1/2/3]: " choice

    local hash
    hash=$(echo "$commits" | head -1 | awk '{print $1}')

    case "$choice" in
        1)
            echo ""
            git revert --no-edit "$hash"
            echo -e "${GREEN}已 revert commit $hash${NC}"
            ;;
        2)
            echo -e "${RED}这会丢弃 $hash 之后的所有变更，确定？(y/N)${NC}"
            read -r confirm
            if [[ "$confirm" == "y" || "$confirm" == "Y" ]]; then
                git reset --hard "${hash}^"
                echo -e "${GREEN}已 reset 到 $hash 之前${NC}"
            else
                echo "已取消"
            fi
            ;;
        *)
            echo "已取消"
            ;;
    esac
}

# ──────────────────────────────────────────────
# 找到下一个未完成的 task
# ──────────────────────────────────────────────
find_next_task() {
    local task_nums
    task_nums=$(grep -oP '^## Task \K[0-9]+(\.[0-9]+)?' "$TASKS_FILE" | sort -t. -k1,1n -k2,2n)

    for i in $task_nums; do
        local status
        status=$(get_task_status "$i")
        if [[ "$status" != "done" ]]; then
            echo "$i"
            return
        fi
    done
    echo "0"
}

# ──────────────────────────────────────────────
# 检查工作区是否干净
# ──────────────────────────────────────────────
check_clean_workdir() {
    cd "$PROJECT_ROOT"
    if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
        echo ""
        echo -e "${YELLOW}警告: 工作区有未提交的变更：${NC}"
        git status --short
        echo ""
        echo -e "${YELLOW}建议先 commit 或 stash 这些变更，再执行 task。继续？(y/N)${NC}"
        read -r confirm
        if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
            exit 0
        fi
    fi
}

# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────

if [[ ! -f "$TASKS_FILE" ]]; then
    echo -e "${RED}错误: 找不到 $TASKS_FILE${NC}"
    exit 1
fi

if [[ ! -f "$GUIDE_FILE" ]]; then
    echo -e "${RED}错误: 找不到 $GUIDE_FILE${NC}"
    exit 1
fi

# 处理参数
case "${1:-}" in
    --status)
        show_status
        exit 0
        ;;
    --log)
        show_log
        exit 0
        ;;
    --revert)
        if [[ -z "${2:-}" ]]; then
            echo -e "${RED}用法: $0 --revert <task_number>${NC}"
            exit 1
        fi
        revert_task "$2"
        exit 0
        ;;
esac

TASK_NUM="${1:-}"

if [[ -z "$TASK_NUM" ]]; then
    TASK_NUM=$(find_next_task)

    if [[ "$TASK_NUM" == "0" ]]; then
        echo ""
        echo -e "${GREEN}所有任务已完成！${NC}"
        show_status
        exit 0
    fi
fi

TASK_TITLE=$(get_task_title "$TASK_NUM")
TASK_STATUS=$(get_task_status "$TASK_NUM")

if [[ -z "$TASK_TITLE" ]]; then
    echo -e "${RED}错误: Task $TASK_NUM 不存在${NC}"
    exit 1
fi

if [[ "$TASK_STATUS" == "done" ]]; then
    echo -e "${YELLOW}$TASK_TITLE 已完成，确定要重新执行？(y/N)${NC}"
    read -r confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        exit 0
    fi
fi

# 检查工作区
check_clean_workdir

# 显示当前状态
show_status

echo -e "${BOLD}>>> 即将执行: $TASK_TITLE${NC}"
echo ""

# 构造 prompt
PROMPT="请阅读 docs/firmware-platform-agent-guide.md，然后执行当前需要完成的任务（Task ${TASK_NUM}）。"

# 切换到 Node 20（happy 需要）
setup_node

# 确认 happy 可用
if ! command -v happy &>/dev/null; then
    echo -e "${RED}错误: happy 未安装。请先运行: npm install -g happy-coder${NC}"
    exit 1
fi

# 启动 happy（--yolo 跳过所有权限确认，手机可实时查看进度）
echo -e "${CYAN}启动 happy (yolo mode) ...${NC}"
echo ""

cd "$PROJECT_ROOT"
happy --yolo "$PROMPT"

# happy 退出后显示状态
echo ""
echo -e "${CYAN}happy 已退出，当前状态：${NC}"
show_status

# 检查是否有新 commit
LATEST_COMMIT=$(git log -1 --oneline --grep="feat(firmware-platform): Task ${TASK_NUM}" 2>/dev/null || true)
if [[ -n "$LATEST_COMMIT" ]]; then
    echo -e "${GREEN}Task ${TASK_NUM} commit: $LATEST_COMMIT${NC}"
else
    echo -e "${YELLOW}未检测到 Task ${TASK_NUM} 的 commit，可能需要手动检查${NC}"
fi
echo ""
