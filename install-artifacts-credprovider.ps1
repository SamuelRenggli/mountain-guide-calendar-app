<#
.SYNOPSIS
    Executes a versioned installation script of artifacts-credprovider from a GitHub release artifact.

.DESCRIPTION
    This script downloads a file from a GitHub artifacts-credprovider release artifact using the provided
    version string and executes the downloaded script, allowing the install script parameters to be passed.

.PARAMETER Version
    The version string of the release (e.g., "1.4.1").

.EXAMPLE
    .\installcredproviderrelease.ps1 -Version 1.3.0 -AddNetFx -Force"
#>

[CmdletBinding(HelpUri = "https://github.com/microsoft/artifacts-credprovider/blob/master/README.md#setup")]
param(
    [Parameter(Mandatory = $false)]
    [string]$Version,

    [Parameter(Mandatory = $false, ValueFromRemainingArguments = $true)]
    [string]$AdditionalParams
)

$ErrorActionPreference = 'Stop'

function Get-ReleaseUrl {
    # Get the file base URL from the GitHub release
    $releaseUrlBase = "https://api.github.com/repos/microsoft/artifacts-credprovider/releases"
    $versionError = "Unable to find the release version $ReleaseVersion from $releaseUrlBase"
    $releaseId = "latest"
    if (![string]::IsNullOrEmpty($ReleaseVersion)) {
        try {
            $releases = Invoke-WebRequest -UseBasicParsing $releaseUrlBase
            $releaseJson = $releases | ConvertFrom-Json
            $correctReleaseVersion = $releaseJson | ? { $_.name -eq $ReleaseVersion }
            $releaseId = $correctReleaseVersion.id
        }
        catch {
            Write-Error $versionError
            return
        }
    }

    if (!$releaseId) {
        Write-Error $versionError
        return
    }

    $releaseUrl = [System.IO.Path]::Combine($releaseUrlBase, $releaseId)
    return $releaseUrl.Replace("\", "/")
}

# Version parameter validation
if ($Version) {
    if ($Version -match '^[vV]') {
        $Version = $Version.Substring(1) # Remove leading 'v' or 'V'
    }

    if ($Version -notmatch '^\d+\.\d+\.\d+') {
        Write-Error "Invalid version. Please use the format #.#.# to override the release version."
        return
    }
}

if ($Version -and ($Version.StartsWith("0.") -or $Version.StartsWith("1."))) {
    # For versions 0.x and 1.0.x, use the last 1.x release URL for backward compatibility
    $ReleaseVersion = "1.4.1"
    $releaseUrl = Get-ReleaseUrl
}
else {
    $ReleaseVersion = $Version
    $releaseUrl = Get-ReleaseUrl
}

$installScriptName = "installcredprovider.ps1"
try {
    Write-Host "Fetching release metadata from $releaseUrl"
    $release = Invoke-WebRequest -UseBasicParsing $releaseUrl
    if (!$release) {
        throw ("Unable to make Web Request to $releaseUrl")
    }
    $releaseJson = $release.Content | ConvertFrom-Json
    if (!$releaseJson) {
        throw ("Unable to get content from JSON")
    }
    $installAsset = $releaseJson.assets | ? { $_.name -eq $installScriptName }
    if (!$installAsset) {
        throw ("Unable to find asset $installScriptName from release JSON object")
    }
    $installHash = $installAsset.digest
    if ($installHash -and $installHash.StartsWith("sha256:")) {
        $expectedHash = $installHash.Substring(7) # Remove "sha256:" prefix
    }
    else {
        $expectedHash = $null
    }
    $installUrl = $installAsset.browser_download_url
    if (!$installUrl) {
        throw ("Unable to find download url from asset $installAsset")
    }
}
catch {
    Write-Error ("Unable to resolve the browser download url from $releaseUrl `nError: " + $_.Exception.Message)
    return
}

# Build the parameters for the executed install script
$paramString = ""
if ($Version) {
    $paramString += "-Version $Version "
}
if ($AdditionalParams) {
    $paramString += $AdditionalParams
}

try {
    # Fetch the install file content
    Write-Host "Fetching $installScriptName from $installUrl..."
    $tempScriptLocation = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), $installScriptName);
    try {
        $client = New-Object System.Net.WebClient
        $client.DownloadFile($installUrl, $tempScriptLocation)
    }
    catch {
        Write-Error "Unable to download $packageSourceUrl to the location $pluginZip"
    }

    if ($null -ne $expectedHash) {
        $actualHash = (Get-FileHash -Path $tempScriptLocation -Algorithm SHA256).Hash
        if ($actualHash.ToLower() -ne $expectedHash.ToLower()) {
            throw "The downloaded $installScriptName hash does not match. `nExpected: $expectedHash, Actual: $actualHash"
        }
    }

    # Execute the script directly from the URL with additional parameters
    Write-Host "Executing $installScriptName..."
    $execCmd = "& { $($tempScriptLocation) $paramString }"
    Invoke-Expression -Command $execCmd
}
catch {
    Write-Error "Failed to fetch, validate, or execute artifacts-credprovider install. Error message: $_"
}

# SIG # Begin signature block
# MIIncAYJKoZIhvcNAQcCoIInYTCCJ10CAQExDzANBglghkgBZQMEAgEFADB5Bgor
# BgEEAYI3AgEEoGswaTA0BgorBgEEAYI3AgEeMCYCAwEAAAQQH8w7YFlLCE63JNLG
# KX7zUQIBAAIBAAIBAAIBAAIBADAxMA0GCWCGSAFlAwQCAQUABCBtb+5vXanaPHqJ
# +3KwxIndSRALuxiI+4SXDMRpdSwswqCCDMkwggYEMIID7KADAgECAhMzAAACHPrN
# xZvoL37EAAAAAAIcMA0GCSqGSIb3DQEBCwUAMFcxCzAJBgNVBAYTAlVTMR4wHAYD
# VQQKExVNaWNyb3NvZnQgQ29ycG9yYXRpb24xKDAmBgNVBAMTH01pY3Jvc29mdCBD
# b2RlIFNpZ25pbmcgUENBIDIwMjQwHhcNMjYwNDE2MTg1OTQxWhcNMjcwNDE1MTg1
# OTQxWjB0MQswCQYDVQQGEwJVUzETMBEGA1UECBMKV2FzaGluZ3RvbjEQMA4GA1UE
# BxMHUmVkbW9uZDEeMBwGA1UEChMVTWljcm9zb2Z0IENvcnBvcmF0aW9uMR4wHAYD
# VQQDExVNaWNyb3NvZnQgQ29ycG9yYXRpb24wggEiMA0GCSqGSIb3DQEBAQUAA4IB
# DwAwggEKAoIBAQDVsZfgOKmM31HPfoWOoNEiw0SlCiIxUMC0I9NMWbucKOw/e9lP
# oAoehQVu6SG65V4EPzrYsnBnFPNoi4/HoOdjhz1qkrEt4I6tEcxXU6oOeY9zGveC
# /3iBeuhLYxM3M/PkcUoebF+Nednm8OkdSPoDu8imViHPQq/8CQUu0WRR4rE+dMRf
# rpVqfmNi2qWCX94T4MsepijGVkwE//tJg0ryAiYdHT34LSnlG/RSBZmQRGWZ5g8j
# qnKjRParSqMft1gvjuUTVgtWNZfgcLFSK5Wa0myrq8OPcgTGGsRgun+tnSS+IxDT
# xVsAPH1OzvPjwomguByhUe/OcvUN0D5Wmp7xAgMBAAGjggGqMIIBpjAOBgNVHQ8B
# Af8EBAMCB4AwHwYDVR0lBBgwFgYKKwYBBAGCN0wIAQYIKwYBBQUHAwMwHQYDVR0O
# BBYEFNoH7a2YDjOSwpkp6DHcmUS7J+0yMFQGA1UdEQRNMEukSTBHMS0wKwYDVQQL
# EyRNaWNyb3NvZnQgSXJlbGFuZCBPcGVyYXRpb25zIExpbWl0ZWQxFjAUBgNVBAUT
# DTIzMDAxMis1MDc1NjkwHwYDVR0jBBgwFoAUf1k/VCHarU/vBeXmo9ctBpQSCDEw
# YAYDVR0fBFkwVzBVoFOgUYZPaHR0cDovL3d3dy5taWNyb3NvZnQuY29tL3BraW9w
# cy9jcmwvTWljcm9zb2Z0JTIwQ29kZSUyMFNpZ25pbmclMjBQQ0ElMjAyMDI0LmNy
# bDBtBggrBgEFBQcBAQRhMF8wXQYIKwYBBQUHMAKGUWh0dHA6Ly93d3cubWljcm9z
# b2Z0LmNvbS9wa2lvcHMvY2VydHMvTWljcm9zb2Z0JTIwQ29kZSUyMFNpZ25pbmcl
# MjBQQ0ElMjAyMDI0LmNydDAMBgNVHRMBAf8EAjAAMA0GCSqGSIb3DQEBCwUAA4IC
# AQAUnEqhaRXe0T3hIJjvdQErEkrA/7bByjn6t5IArODkkRjzkYwtKMc2yYj2quaN
# rLutWw2YZcngKPy1b71YyDJQTy4NDRwaSh9Tw5thrk3NmcPrAHia5vtcBJ1CgtKK
# 7mQbIcQ22d/N3813ayCDDFewu1+jsZmX+r/aTEqaOM4TVxVtRSkuCy8nAXKuChOK
# Li/zA4XuH8iEYqIsj2YoNaeSxVmeGiERXpKdo3dDmYi0kO5w2D8VS4c3+9h6gElY
# BaAAg/dYErBg27qT3vv0zRDJhJufvCNylA8S7/+8H5E/PV5cng6na9VV/w9OV3qu
# uND6zdGa2EX38Glp50F9AIQk3p2xXmcvorDeM4XJ7UlWYBi6g80J1SSOQnInCYFE
# msfUNn3+1AaTJKSJL83quKArTac2pKhu0Yzzzrzo6HrsRiQKzpnRBb1/dMa6P3hz
# 75XbMRBctNsFhZC07WCmjExdLg2eHW5uV0TY8D5+6wozJf7vF3+WHkYPO85Z+BC6
# U4FkNbYNycZ9cE4j1tXRdyDCfml6c0HWPHjNVDObrv9lKt3qUqFpX38VCqVCyNOO
# 1UcXfQiVjJw32U2WUKZjt/neJKHEBsm9kFsLuWzkQ53+qcaSaytmsCnk2gOglrlD
# 5d3kKyvvAw+rzm0lT8K38P6PLxfZQHhu4W8dV7Av8N2ZmDCCBr0wggSloAMCAQIC
# EzMAAAA5O7Y3Gb8GHWcAAAAAADkwDQYJKoZIhvcNAQEMBQAwgYgxCzAJBgNVBAYT
# AlVTMRMwEQYDVQQIEwpXYXNoaW5ndG9uMRAwDgYDVQQHEwdSZWRtb25kMR4wHAYD
# VQQKExVNaWNyb3NvZnQgQ29ycG9yYXRpb24xMjAwBgNVBAMTKU1pY3Jvc29mdCBS
# b290IENlcnRpZmljYXRlIEF1dGhvcml0eSAyMDExMB4XDTI0MDgwODIwNTQxOFoX
# DTM2MDMyMjIyMTMwNFowVzELMAkGA1UEBhMCVVMxHjAcBgNVBAoTFU1pY3Jvc29m
# dCBDb3Jwb3JhdGlvbjEoMCYGA1UEAxMfTWljcm9zb2Z0IENvZGUgU2lnbmluZyBQ
# Q0EgMjAyNDCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBANgBnB7jOMeq
# lRYHNa265v4IY9fH8TKhemHfPINe1gpLaV3dhg324WwH06LcHbpnsBukCDNitryo
# 0dtS/EW6I/yEL/bLSY8hKpbfQuWusBPr9qazYcDxCW/qnjb5JsI1s8bNOg3bVATv
# QVL4tcf03aTycsz8QeCdM0l/yHRObJ9QqazM1r6VPEOJ7LL+uEEb73w6QCuhs89a
# 1uv1zerOYMnsneRRwCbpyW11IcggU0cRKDDq1pjVJzIbIF6+oiXXbReOsgeI8zu1
# FyQfK0fVkaya8SmVHQ/tOf23mZ4W9k0Ri22QW9p3UgSC5OUDktKxxcCmGL6tXLfO
# GSWHIIV4YrTJTT6PNty5REojHJuZHArkF9VnHTERWoTjAzfI3kP+5b4alUdhgAZ7
# ttOu1bVnXfHaqPYl2rPs20ji03LOVWsh/radgE17es5hL+t6lV0eVHrVhsssROWJ
# uz2MXMCt7iw7lFPG9LXKGjsmonn2gotGdHIuEg5JnJMJVmixd5LRlkmgYRZKzhxS
# CwyoGIq0PhaA7Y+VPct5pCHkijcIIDm0nlkK+0KyepolcqGm0T/GYQRMhHJlGOOm
# VQop36wUVUYklUy++vDWeEgEo4s7hxN6mIbf2MSIQ/iIfMZgJxC69oukMUXCrOC3
# SkE/xIkgpfl22MM1itkZ35nNXkMolU1lAgMBAAGjggFOMIIBSjAOBgNVHQ8BAf8E
# BAMCAYYwEAYJKwYBBAGCNxUBBAMCAQAwHQYDVR0OBBYEFH9ZP1Qh2q1P7wXl5qPX
# LQaUEggxMBkGCSsGAQQBgjcUAgQMHgoAUwB1AGIAQwBBMA8GA1UdEwEB/wQFMAMB
# Af8wHwYDVR0jBBgwFoAUci06AjGQQ7kUBU7h6qfHMdEjiTQwWgYDVR0fBFMwUTBP
# oE2gS4ZJaHR0cDovL2NybC5taWNyb3NvZnQuY29tL3BraS9jcmwvcHJvZHVjdHMv
# TWljUm9vQ2VyQXV0MjAxMV8yMDExXzAzXzIyLmNybDBeBggrBgEFBQcBAQRSMFAw
# TgYIKwYBBQUHMAKGQmh0dHA6Ly93d3cubWljcm9zb2Z0LmNvbS9wa2kvY2VydHMv
# TWljUm9vQ2VyQXV0MjAxMV8yMDExXzAzXzIyLmNydDANBgkqhkiG9w0BAQwFAAOC
# AgEAFJQfOChP7onn6fLIMKrSlN1WYKwDFgAddymOUO3FrM8d7B/W/iQ6DxXsDn7D
# 5W4wMwYeLystcEqfkjz4NURRgazyMu5yRzQh4LqjA4tStTcJh1opExo7nn5PuPBY
# nbu0+THSuVHTe0VTTPVhily/piFrDo3axQ9P4C+Ol5yet+2gTfekICS5xS+cYfSI
# vgn0JksVBVMYVI5QFu/qhnLhsEFEUzG8fvv0hjgkO+lkpV9ty6GkN4vdnd7ya6Q6
# aR9y34aiM1qmxaxBi6OUnyNl6fkuun/diTFnYDLTppOkr/mg5WSfCiDVMNCxtj4w
# PKC5OmHm1DQIt/MNokbbH3UGsFP1QbzsLocuSqLCvH09Io3fDPTmscR9Y75G4qX7
# RTX8AdBPo0I6OEojf39zuFZt0qOHm65YWQE69cZM2ueE1MB05dNNgHK9gTE7zKvK
# /fg8B2qjW88MT/WF5V5uvZGtqa9FSL2RazArA+rDPuf6JGYz4HpgMZHB4S6szWSK
# YBv0VisCzfxgeU+dquXW9bd0auYlOB58DPcOYKdc3Se94g+xL4pcEhbB54JOgAkw
# YTu/9dLeH2pDqeJZAABVDWRQCaXfO5LgyKwKCLYXpigrZYCjUSBcr+Ve8PFWMhVT
# Ql0v4q8J/AUmQN5W4n101cY2L4A7GTQG1h32HHAvfQESWP0xghn9MIIZ+QIBATBu
# MFcxCzAJBgNVBAYTAlVTMR4wHAYDVQQKExVNaWNyb3NvZnQgQ29ycG9yYXRpb24x
# KDAmBgNVBAMTH01pY3Jvc29mdCBDb2RlIFNpZ25pbmcgUENBIDIwMjQCEzMAAAIc
# +s3Fm+gvfsQAAAAAAhwwDQYJYIZIAWUDBAIBBQCggbAwGQYJKoZIhvcNAQkDMQwG
# CisGAQQBgjcCAQQwHAYKKwYBBAGCNwIBCzEOMAwGCisGAQQBgjcCARUwLwYJKoZI
# hvcNAQkEMSIEIB7fMLFD5GdiCKEtu438Yekv29UykLCjuo1I3uxTeyeDMEQGCisG
# AQQBgjcCAQwxNjA0oBSAEgBNAGkAYwByAG8AcwBvAGYAdKEcgBpodHRwczovL3d3
# dy5taWNyb3NvZnQuY29tIDANBgkqhkiG9w0BAQEFAASCAQANsjASScpz3ZB8OZHf
# zd6NK+ab4ubSwiC277fg6fb5ybHpLHVwR1cqyEun8rlOpn3ZH5lFIS0FTPuEIIj2
# TzYMItFTWTPU4GSdCu8Kf1PVy9ARF4PVY7pucZed6IlE2pLNfxjgSc0HiQP4hsDQ
# XRzyUiPOmHKrUpofCeRWNDt7snMxjhhMtD/tUWOoeR8PWe61+wEe7Kvwm2ZAKBUX
# ClkD/fDkbdBQCm0oW2XSZ0vBqCC2wcpB3hODDNhRzYalKxx7UCk+TBwY+vM3rrJm
# 8h/Z68lKzJGKJ08BujzyV5kGtwkq7pqwFoQbwEgUjaq2llLV3ONmRqNnrpWJbQ+q
# FOYRoYIXrTCCF6kGCisGAQQBgjcDAwExgheZMIIXlQYJKoZIhvcNAQcCoIIXhjCC
# F4ICAQMxDzANBglghkgBZQMEAgEFADCCAVoGCyqGSIb3DQEJEAEEoIIBSQSCAUUw
# ggFBAgEBBgorBgEEAYRZCgMBMDEwDQYJYIZIAWUDBAIBBQAEII1b1N8Zi7F+AjCy
# wQtwXahdXdtLyURtoWBkLG1piyR8AgZqXgHJ3rwYEzIwMjYwNzI0MTc0NDAwLjg5
# M1owBIACAfSggdmkgdYwgdMxCzAJBgNVBAYTAlVTMRMwEQYDVQQIEwpXYXNoaW5n
# dG9uMRAwDgYDVQQHEwdSZWRtb25kMR4wHAYDVQQKExVNaWNyb3NvZnQgQ29ycG9y
# YXRpb24xLTArBgNVBAsTJE1pY3Jvc29mdCBJcmVsYW5kIE9wZXJhdGlvbnMgTGlt
# aXRlZDEnMCUGA1UECxMeblNoaWVsZCBUU1MgRVNOOjU1MUEtMDVFMC1EOTQ3MSUw
# IwYDVQQDExxNaWNyb3NvZnQgVGltZS1TdGFtcCBTZXJ2aWNloIIR+zCCBygwggUQ
# oAMCAQICEzMAAAIb0LK4Amf3cs8AAQAAAhswDQYJKoZIhvcNAQELBQAwfDELMAkG
# A1UEBhMCVVMxEzARBgNVBAgTCldhc2hpbmd0b24xEDAOBgNVBAcTB1JlZG1vbmQx
# HjAcBgNVBAoTFU1pY3Jvc29mdCBDb3Jwb3JhdGlvbjEmMCQGA1UEAxMdTWljcm9z
# b2Z0IFRpbWUtU3RhbXAgUENBIDIwMTAwHhcNMjUwODE0MTg0ODMwWhcNMjYxMTEz
# MTg0ODMwWjCB0zELMAkGA1UEBhMCVVMxEzARBgNVBAgTCldhc2hpbmd0b24xEDAO
# BgNVBAcTB1JlZG1vbmQxHjAcBgNVBAoTFU1pY3Jvc29mdCBDb3Jwb3JhdGlvbjEt
# MCsGA1UECxMkTWljcm9zb2Z0IElyZWxhbmQgT3BlcmF0aW9ucyBMaW1pdGVkMScw
# JQYDVQQLEx5uU2hpZWxkIFRTUyBFU046NTUxQS0wNUUwLUQ5NDcxJTAjBgNVBAMT
# HE1pY3Jvc29mdCBUaW1lLVN0YW1wIFNlcnZpY2UwggIiMA0GCSqGSIb3DQEBAQUA
# A4ICDwAwggIKAoICAQCOxZ3nZlmTMHld7mD+XYaw6MDPfSyDqNXF8UlX7DjEgNXJ
# ojcs7xsimbNi6XcBkeDnRQhDw+tJFkalCoWRE276jdgoniDa4ZgFGSwecdhHS5VI
# JCDnxOGRjJ6mUZfegC8ZFW48ilC0CJOxHvoD+B2hTscPARtvvdsnBPKtsoeFH5Zo
# zL0NAcjiTlCjj5tkOzSSPvpu+Em90ZT5LzPFAGntQCGMmcWorEi6xIhMTvMIJHjb
# YQuGSFVU4WorbDqHUwC8gt7vqHFEhw+PRIEvavw723HmeNTj62DasB1TXnembKGp
# rN2lRxxgET3ANEVR3970KhbHtN2dSJwH4xqLtFPqqx7t7loapfUHtueP9ke+ut8X
# 4EkQiVL2INcBSB6S9dn4VmaO8vA/5037T9yuH76vh7wWScXsRfogl+eY14M3/rxn
# n2RtonV/4/macph/J0J5mbGsalLS1paQOTfoPeM9Vl+W/Gtz7WuEIiUzm/1qAsQU
# jXZCIFN+k4E4GvcAYI+T54fT6Vq2NBqO6D7b8EPXapvzbnTQtDK1RZPai1r8didG
# BK/WO9nT92aXUWzFZjM6cKuN90H/s3qk3JK3i+f48Y3p0UuKbuTGiz4H1Z9A97Mm
# Ld+4rLIMAH3NIc+PVm7ydl95xkn26bjOPsMWC8ldMNOcbmqUbhl1sVFr+ut/OQID
# AQABo4IBSTCCAUUwHQYDVR0OBBYEFLa+n3f+XEumk0rw6Rq4nYC82YhQMB8GA1Ud
# IwQYMBaAFJ+nFV0AXmJdg/Tl0mWnG1M1GelyMF8GA1UdHwRYMFYwVKBSoFCGTmh0
# dHA6Ly93d3cubWljcm9zb2Z0LmNvbS9wa2lvcHMvY3JsL01pY3Jvc29mdCUyMFRp
# bWUtU3RhbXAlMjBQQ0ElMjAyMDEwKDEpLmNybDBsBggrBgEFBQcBAQRgMF4wXAYI
# KwYBBQUHMAKGUGh0dHA6Ly93d3cubWljcm9zb2Z0LmNvbS9wa2lvcHMvY2VydHMv
# TWljcm9zb2Z0JTIwVGltZS1TdGFtcCUyMFBDQSUyMDIwMTAoMSkuY3J0MAwGA1Ud
# EwEB/wQCMAAwFgYDVR0lAQH/BAwwCgYIKwYBBQUHAwgwDgYDVR0PAQH/BAQDAgeA
# MA0GCSqGSIb3DQEBCwUAA4ICAQBmRTVfFAPg5MzcZOG3fZNdKEh88Ggx9KwWwFCo
# U5mosk7HIk6WUgEWmam860Y0+QLlnyV0bxoKm+AU2j+MNZ5PkWJbnd0CP0qdnGmx
# Dc9/l9HNIYdFzEQw51chXMMnBxlRfRyN/GdrvJ02/x5cH9eTobpLKtHY4fpLUscx
# bXWbdS8oX54uMg+XjmvGKa4MKgR35p3SU4BcDn+9k4o3mf949h4/QtFyFlfRDofy
# f9mZI8yVuWLcw7znVDT1GZP9kYdr78V3L5YsOvBxjKRX2ZTL/hNvArDoW11Hpk8f
# Ex0iLWmTxjaYL8bMKrQsKwfS5MV5DpDs1zcxGYRH/eYtZSFtpYeBfUVthyG9HbZv
# 4G6n5g9HlD/QGFpoA3oAgF9waz67+cmggHLJkoDxxPIKadQj/i9boPi/LCDdcEV/
# h/YPAUfL96+wL7nwoyX6TbBrTlfaQrRP9sI8uFqi/1lfKhtrB804tgaJq4pPYVa9
# vBnMcgUJPGMHDDo+3m5G8IT+OdRx//GGU4YyfqIo71e3j29lMTZJ8gGT/fiItNEE
# noftoY9NNCfNrc59a7X91HJwLpaXmiezc+OcZdNIpLFeWUk+aDpH+6Uaic/9QJig
# nqY34ReN/IMs9cuqyv3X5VMbWtjNEKM/AEUAe/gQjBoTRqMKt/vl5QYjf6hdTRQ/
# quWhnzCCB3EwggVZoAMCAQICEzMAAAAVxedrngKbSZkAAAAAABUwDQYJKoZIhvcN
# AQELBQAwgYgxCzAJBgNVBAYTAlVTMRMwEQYDVQQIEwpXYXNoaW5ndG9uMRAwDgYD
# VQQHEwdSZWRtb25kMR4wHAYDVQQKExVNaWNyb3NvZnQgQ29ycG9yYXRpb24xMjAw
# BgNVBAMTKU1pY3Jvc29mdCBSb290IENlcnRpZmljYXRlIEF1dGhvcml0eSAyMDEw
# MB4XDTIxMDkzMDE4MjIyNVoXDTMwMDkzMDE4MzIyNVowfDELMAkGA1UEBhMCVVMx
# EzARBgNVBAgTCldhc2hpbmd0b24xEDAOBgNVBAcTB1JlZG1vbmQxHjAcBgNVBAoT
# FU1pY3Jvc29mdCBDb3Jwb3JhdGlvbjEmMCQGA1UEAxMdTWljcm9zb2Z0IFRpbWUt
# U3RhbXAgUENBIDIwMTAwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQDk
# 4aZM57RyIQt5osvXJHm9DtWC0/3unAcH0qlsTnXIyjVX9gF/bErg4r25PhdgM/9c
# T8dm95VTcVrifkpa/rg2Z4VGIwy1jRPPdzLAEBjoYH1qUoNEt6aORmsHFPPFdvWG
# UNzBRMhxXFExN6AKOG6N7dcP2CZTfDlhAnrEqv1yaa8dq6z2Nr41JmTamDu6Gnsz
# rYBbfowQHJ1S/rboYiXcag/PXfT+jlPP1uyFVk3v3byNpOORj7I5LFGc6XBpDco2
# LXCOMcg1KL3jtIckw+DJj361VI/c+gVVmG1oO5pGve2krnopN6zL64NF50ZuyjLV
# wIYwXE8s4mKyzbnijYjklqwBSru+cakXW2dg3viSkR4dPf0gz3N9QZpGdc3EXzTd
# EonW/aUgfX782Z5F37ZyL9t9X4C626p+Nuw2TPYrbqgSUei/BQOj0XOmTTd0lBw0
# gg/wEPK3Rxjtp+iZfD9M269ewvPV2HM9Q07BMzlMjgK8QmguEOqEUUbi0b1qGFph
# AXPKZ6Je1yh2AuIzGHLXpyDwwvoSCtdjbwzJNmSLW6CmgyFdXzB0kZSU2LlQ+QuJ
# YfM2BjUYhEfb3BvR/bLUHMVr9lxSUV0S2yW6r1AFemzFER1y7435UsSFF5PAPBXb
# GjfHCBUYP3irRbb1Hode2o+eFnJpxq57t7c+auIurQIDAQABo4IB3TCCAdkwEgYJ
# KwYBBAGCNxUBBAUCAwEAATAjBgkrBgEEAYI3FQIEFgQUKqdS/mTEmr6CkTxGNSnP
# EP8vBO4wHQYDVR0OBBYEFJ+nFV0AXmJdg/Tl0mWnG1M1GelyMFwGA1UdIARVMFMw
# UQYMKwYBBAGCN0yDfQEBMEEwPwYIKwYBBQUHAgEWM2h0dHA6Ly93d3cubWljcm9z
# b2Z0LmNvbS9wa2lvcHMvRG9jcy9SZXBvc2l0b3J5Lmh0bTATBgNVHSUEDDAKBggr
# BgEFBQcDCDAZBgkrBgEEAYI3FAIEDB4KAFMAdQBiAEMAQTALBgNVHQ8EBAMCAYYw
# DwYDVR0TAQH/BAUwAwEB/zAfBgNVHSMEGDAWgBTV9lbLj+iiXGJo0T2UkFvXzpoY
# xDBWBgNVHR8ETzBNMEugSaBHhkVodHRwOi8vY3JsLm1pY3Jvc29mdC5jb20vcGtp
# L2NybC9wcm9kdWN0cy9NaWNSb29DZXJBdXRfMjAxMC0wNi0yMy5jcmwwWgYIKwYB
# BQUHAQEETjBMMEoGCCsGAQUFBzAChj5odHRwOi8vd3d3Lm1pY3Jvc29mdC5jb20v
# cGtpL2NlcnRzL01pY1Jvb0NlckF1dF8yMDEwLTA2LTIzLmNydDANBgkqhkiG9w0B
# AQsFAAOCAgEAnVV9/Cqt4SwfZwExJFvhnnJL/Klv6lwUtj5OR2R4sQaTlz0xM7U5
# 18JxNj/aZGx80HU5bbsPMeTCj/ts0aGUGCLu6WZnOlNN3Zi6th542DYunKmCVgAD
# sAW+iehp4LoJ7nvfam++Kctu2D9IdQHZGN5tggz1bSNU5HhTdSRXud2f8449xvNo
# 32X2pFaq95W2KFUn0CS9QKC/GbYSEhFdPSfgQJY4rPf5KYnDvBewVIVCs/wMnosZ
# iefwC2qBwoEZQhlSdYo2wh3DYXMuLGt7bj8sCXgU6ZGyqVvfSaN0DLzskYDSPeZK
# PmY7T7uG+jIa2Zb0j/aRAfbOxnT99kxybxCrdTDFNLB62FD+CljdQDzHVG2dY3RI
# LLFORy3BFARxv2T5JL5zbcqOCb2zAVdJVGTZc9d/HltEAY5aGZFrDZ+kKNxnGSgk
# ujhLmm77IVRrakURR6nxt67I6IleT53S0Ex2tVdUCbFpAUR+fKFhbHP+CrvsQWY9
# af3LwUFJfn6Tvsv4O+S3Fb+0zj6lMVGEvL8CwYKiexcdFYmNcP7ntdAoGokLjzba
# ukz5m/8K6TT4JDVnK+ANuOaMmdbhIurwJ0I9JZTmdHRbatGePu1+oDEzfbzL6Xu/
# OHBE0ZDxyKs6ijoIYn/ZcGNTTY3ugm2lBRDBcQZqELQdVTNYs6FwZvKhggNWMIIC
# PgIBATCCAQGhgdmkgdYwgdMxCzAJBgNVBAYTAlVTMRMwEQYDVQQIEwpXYXNoaW5n
# dG9uMRAwDgYDVQQHEwdSZWRtb25kMR4wHAYDVQQKExVNaWNyb3NvZnQgQ29ycG9y
# YXRpb24xLTArBgNVBAsTJE1pY3Jvc29mdCBJcmVsYW5kIE9wZXJhdGlvbnMgTGlt
# aXRlZDEnMCUGA1UECxMeblNoaWVsZCBUU1MgRVNOOjU1MUEtMDVFMC1EOTQ3MSUw
# IwYDVQQDExxNaWNyb3NvZnQgVGltZS1TdGFtcCBTZXJ2aWNloiMKAQEwBwYFKw4D
# AhoDFQCGhXqvj0zgYF3jUrVFgHVnR/jO4KCBgzCBgKR+MHwxCzAJBgNVBAYTAlVT
# MRMwEQYDVQQIEwpXYXNoaW5ndG9uMRAwDgYDVQQHEwdSZWRtb25kMR4wHAYDVQQK
# ExVNaWNyb3NvZnQgQ29ycG9yYXRpb24xJjAkBgNVBAMTHU1pY3Jvc29mdCBUaW1l
# LVN0YW1wIFBDQSAyMDEwMA0GCSqGSIb3DQEBCwUAAgUA7g3F4zAiGA8yMDI2MDcy
# NDExMDcxNVoYDzIwMjYwNzI1MTEwNzE1WjB0MDoGCisGAQQBhFkKBAExLDAqMAoC
# BQDuDcXjAgEAMAcCAQACAgJrMAcCAQACAhL4MAoCBQDuDxdjAgEAMDYGCisGAQQB
# hFkKBAIxKDAmMAwGCisGAQQBhFkKAwKgCjAIAgEAAgMHoSChCjAIAgEAAgMBhqAw
# DQYJKoZIhvcNAQELBQADggEBAImiw7qibU+5IXwcIWnpw1/6dp/5fsiFJn73+3kY
# Mq/7dCARru9s4n6hEgS6pPchrIVShbEt/VO0/82Tfw0HTP09bYpIweeI5HPIrU8o
# BE7OgMq9UTfPbXG8hp9yMODpuBrQV9Glt6LSD09FZg5EB9ST8fZdDJ0xvZOCQ+y/
# bvZ/rxzKufl+glY0tbtazJagcAzRTr52CsSLiQ3ALSIzfRWFnfEkm7v23VFiEHEh
# XIlIxcR4ejivdwUZxsi5GUjzusjr5C9aeTw30wk8Q95B8gTRm5pD5RKjKu9GvJ5s
# pTYTZMntMXgDFl15nBdOXK6pyIxpRTPeb3uhI08wWVnRj5MxggQNMIIECQIBATCB
# kzB8MQswCQYDVQQGEwJVUzETMBEGA1UECBMKV2FzaGluZ3RvbjEQMA4GA1UEBxMH
# UmVkbW9uZDEeMBwGA1UEChMVTWljcm9zb2Z0IENvcnBvcmF0aW9uMSYwJAYDVQQD
# Ex1NaWNyb3NvZnQgVGltZS1TdGFtcCBQQ0EgMjAxMAITMwAAAhvQsrgCZ/dyzwAB
# AAACGzANBglghkgBZQMEAgEFAKCCAUowGgYJKoZIhvcNAQkDMQ0GCyqGSIb3DQEJ
# EAEEMC8GCSqGSIb3DQEJBDEiBCCC8qeJN37/DUCZYxEl/O/Y+GjRm4E7R9noTR17
# t/DbeDCB+gYLKoZIhvcNAQkQAi8xgeowgecwgeQwgb0EIDAlFJW4PaOYxxAIVd0u
# 4kDAOlRU1nptzp18lTzdDYuAMIGYMIGApH4wfDELMAkGA1UEBhMCVVMxEzARBgNV
# BAgTCldhc2hpbmd0b24xEDAOBgNVBAcTB1JlZG1vbmQxHjAcBgNVBAoTFU1pY3Jv
# c29mdCBDb3Jwb3JhdGlvbjEmMCQGA1UEAxMdTWljcm9zb2Z0IFRpbWUtU3RhbXAg
# UENBIDIwMTACEzMAAAIb0LK4Amf3cs8AAQAAAhswIgQg/TX27ls4q0I9HGkUUyoT
# pH/DfZdqfPx05u2SrwRyPyUwDQYJKoZIhvcNAQELBQAEggIAWrfSV8tc6BQNxmbq
# LYHiEae/NmfPXrA1WXfJfR0x8UpV9NmY8vBpiRqKqQuLmxMy80cDFCMCnD8o6XRU
# omfz2XJACgL8RJOnPR/y/kuHEAzyN1HH7hV/Iijc3cMQz6ZCn5kB/DNByaspMVCO
# yG6/iKn9HSGY3CfcO6hdsELiPVnGuom4XzhthOsIsxHpsJGvOOtmpfX5ZNXYdBIh
# wTWSkGf/+ttFhqSzGldODVsgIWLi9ucHZjtg+Dz3KXXWG20HtBBdEw0k8fmWVwVB
# 0eUj+atSDat5XfFChfDiviEzzYpApFCZWArwmS/xt886cV2mRqP81QsmSjpcsX2Y
# feF1SzcV/gND52SDHt+0Mljhril+4kkUlz7mFMY7JzLc+j9dgGh2PHiLmQmhMWjf
# 91PCZtnEpx+DNfR2SIrMHP6QUxxyiXnJd6x+E9DpayXLETAWuH19KfDSTJiEot2e
# ZTZbYwQAykXeUU4MsF7GrxmC8j9d0S90PD8kZT1PBHhtVomw73/lPYZCWz2c9anf
# lyo0fYnlNOFAf7HqDnxjnrQsRVZOLIwHfETsfN2lSAfk2tw1uKlj6qAhwJofi25+
# jYY7F9I86skYz4As+KdN0Ifr6Og6oPWyjL3svAVCdhVK2USeeyd6iWcbLbGCzEiz
# 8VqKMHwkUAppwbANYc0gyOobMUo=
# SIG # End signature block
