# replace-settings.ps1
$sourcePath = "C:\Users\ttelab\.claude\settings-fozu.json"
$targetPath = "C:\Users\ttelab\.claude\settings.json"

# 检查源文件是否存在
if (Test-Path $sourcePath) {
    # 复制文件内容（覆盖目标文件）
    Copy-Item -Path $sourcePath -Destination $targetPath -Force
    Write-Host "成功将 settings-fozu.json 的内容复制到 settings.json" -ForegroundColor Green
} else {
    Write-Host "错误：源文件 settings-fozu.json 不存在！" -ForegroundColor Red
}