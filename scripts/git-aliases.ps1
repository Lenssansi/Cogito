# 一次性配置 Cogito 用到的 git 别名(换新电脑跑一遍即可)。
# 这些别名是「全局」的:配一次,你本机所有仓库都能用。
# 用法:在仓库根目录执行  ->  powershell -ExecutionPolicy Bypass -File scripts\git-aliases.ps1

git config --global alias.st "status -sb"
git config --global alias.aa "add -A"
git config --global alias.cm "commit -m"
git config --global alias.lg "log --oneline --graph --decorate"
git config --global alias.graph "log --oneline --graph --decorate --all"
git config --global alias.last "log -1 --stat"
git config --global alias.unstage "restore --staged"
git config --global alias.undo "reset --soft HEAD~1"

Write-Host "OK - git aliases configured:" -ForegroundColor Green
git config --global --get-regexp "^alias\."
