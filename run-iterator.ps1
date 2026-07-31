# D:\project\operator-job\tasks\operator-common-iterator-claude\run-iterator.ps1

$workDir = "D:\project\operator-job\tasks\operator-common-iterator-claude"
$scriptPath = "scripts/iterate_dir_fresh.sh"
$arguments = "two-operators --max-iterations 3 --case-count 10"

# 切换到工作目录
Set-Location $workDir

# 使用 Git Bash 执行脚本
$bashPath = "C:\Program Files\Git\bin\bash.exe"

if (Test-Path $bashPath) {
    $fullCommand = "$scriptPath $arguments"
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] Execute command: $fullCommand" -ForegroundColor Yellow
    Write-Host "[$timestamp] Working directory: $workDir" -ForegroundColor Yellow
    
    # 执行命令
    & $bashPath -c "$fullCommand"
    
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] Command execution completed" -ForegroundColor Green
} else {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] Error: Git Bash not found!" -ForegroundColor Red
    Write-Host "[$timestamp] Tried path: $bashPath" -ForegroundColor Red
    
    # 尝试查找 Git Bash 的其他可能位置
    $alternativePaths = @(
        "C:\Program Files\Git\bin\bash.exe",
        "C:\Program Files (x86)\Git\bin\bash.exe",
        "$env:USERPROFILE\AppData\Local\Programs\Git\bin\bash.exe"
    )
    
    Write-Host "[$timestamp] Trying alternative paths..." -ForegroundColor Yellow
    foreach ($altPath in $alternativePaths) {
        if (Test-Path $altPath) {
            Write-Host "[$timestamp] Found Git Bash at: $altPath" -ForegroundColor Green
            $bashPath = $altPath
            # 重新执行命令
            $fullCommand = "$scriptPath $arguments"
            & $bashPath -c "$fullCommand"
            break
        }
    }
}