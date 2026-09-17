function ConvertTo-PackageRelativePath {
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string] $PackageRoot,
        [Parameter(Mandatory)] [string] $Path
    )

    $rootPath = [IO.Path]::GetFullPath($PackageRoot)
    if (-not $rootPath.EndsWith([string][IO.Path]::DirectorySeparatorChar)) {
        $rootPath += [IO.Path]::DirectorySeparatorChar
    }
    $filePath = [IO.Path]::GetFullPath($Path)
    [Uri] $rootUri = $rootPath
    [Uri] $fileUri = $filePath
    $relativeUri = $rootUri.MakeRelativeUri($fileUri)
    if ($relativeUri.IsAbsoluteUri) {
        throw "Package path must be relative to the package root: $filePath"
    }

    return [Uri]::UnescapeDataString($relativeUri.ToString()).Replace("\", "/")
}

function Get-PackageIsolatedPath {
    [CmdletBinding()]
    [OutputType([string])]
    param()

    $windowsRoot = [Environment]::GetFolderPath("Windows")
    return @(
        (Join-Path $windowsRoot "System32"),
        (Join-Path $windowsRoot "System32/WindowsPowerShell/v1.0"),
        (Join-Path $windowsRoot "System32/Wbem")
    ) -join [IO.Path]::PathSeparator
}
