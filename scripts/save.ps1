# 一键提交:暂存全部改动 + 提交一个快照。
# 用法:  scripts\save.ps1 "提交信息"
# 提示:不确定改了什么时,先 `git st` 复核,再用本脚本。
param([Parameter(Mandatory = $true)][string]$Message)

git add -A
Write-Host "--- staged for this commit: ---" -ForegroundColor Cyan
git status -sb
git commit -m $Message
