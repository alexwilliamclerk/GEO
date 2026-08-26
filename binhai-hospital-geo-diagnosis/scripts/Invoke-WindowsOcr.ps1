param(
    [Parameter(Mandatory = $true)]
    [string]$InputDirectory,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [int]$Limit = 0
)

$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.FileAccessMode, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime] | Out-Null

$asTaskMethod = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1
})[0]

function Await-Result {
    param(
        [Parameter(Mandatory = $true)]$Operation,
        [Parameter(Mandatory = $true)][Type]$ResultType
    )

    $method = $asTaskMethod.MakeGenericMethod($ResultType)
    $task = $method.Invoke($null, @($Operation))
    $task.Wait()
    return $task.Result
}

$inputRoot = (Resolve-Path -LiteralPath $InputDirectory).Path
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$outputRoot = (Resolve-Path -LiteralPath $OutputDirectory).Path

$language = [Windows.Globalization.Language]::new('zh-Hans-CN')
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
if ($null -eq $engine) {
    throw 'Windows OCR engine for zh-Hans-CN is unavailable.'
}

$files = @(Get-ChildItem -LiteralPath $inputRoot -Recurse -File | Where-Object {
    $_.Extension -in '.jpg', '.jpeg', '.png'
} | Sort-Object FullName)

if ($Limit -gt 0) {
    $files = @($files | Select-Object -First $Limit)
}

$inventory = @(foreach ($file in $files) {
    $storageFile = Await-Result ([Windows.Storage.StorageFile]::GetFileFromPathAsync($file.FullName)) ([Windows.Storage.StorageFile])
    $stream = Await-Result ($storageFile.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $decoder = Await-Result ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await-Result ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        try {
            $result = Await-Result ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            $relative = $file.FullName.Substring($inputRoot.Length).TrimStart([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
            $relativeText = [IO.Path]::ChangeExtension($relative, '.txt')
            $outputPath = Join-Path $outputRoot $relativeText
            $outputParent = Split-Path -Parent $outputPath
            New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
            [IO.File]::WriteAllText($outputPath, $result.Text, [Text.UTF8Encoding]::new($false))

            [pscustomobject]@{
                image_path = $relative.Replace('\', '/')
                ocr_path = $relativeText.Replace('\', '/')
                character_count = $result.Text.Length
                line_count = $result.Lines.Count
                status = 'completed'
            }
        }
        finally {
            if ($null -ne $bitmap) { $bitmap.Dispose() }
        }
    }
    finally {
        if ($null -ne $stream) { $stream.Dispose() }
    }
})

$inventoryPath = Join-Path $outputRoot 'ocr-inventory.csv'
$inventory | Export-Csv -LiteralPath $inventoryPath -NoTypeInformation -Encoding utf8
Write-Output "OCR complete: $($inventory.Count) files -> $outputRoot"
