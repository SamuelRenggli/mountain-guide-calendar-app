# Test API endpoints
$baseUrl = "http://localhost:8000"

# Test 1: Check if API is responding
Write-Host "Test 1: Checking API health..."
try {
    $response = Invoke-WebRequest -Uri "$baseUrl/openapi.json" -UseBasicParsing
    Write-Host "[OK] API is responding (Status: $($response.StatusCode))"
} catch {
    Write-Host "[FAIL] API is not responding: $($_.Exception.Message)"
    exit 1
}

# Test 2: Create a guide (user)
Write-Host "`nTest 2: Creating a guide..."
$guideData = @{
    email = "bergfuehrer1@example.com"
    name = "Hans Müller"
    ical_url = $null
} | ConvertTo-Json

try {
    $response = Invoke-WebRequest -Uri "$baseUrl/guides/" `
        -Method POST `
        -ContentType "application/json" `
        -Body $guideData `
        -UseBasicParsing
    
    $guide = $response.Content | ConvertFrom-Json
    Write-Host "[OK] Guide created successfully"
    Write-Host "  Guide ID: $($guide.id)"
    Write-Host "  Name: $($guide.name)"
    $guideId = $guide.id
} catch {
    Write-Host "[FAIL] Failed to create guide: $($_.Exception.Response.StatusCode)"
    $_.Exception.Response.GetResponseStream() | ForEach-Object { 
        $reader = [System.IO.StreamReader]$_
        $responseBody = $reader.ReadToEnd()
        Write-Host "Response: $responseBody"
    }
    exit 1
}

# Test 3: Get the guide
Write-Host "`nTest 3: Retrieving guide..."
try {
    $response = Invoke-WebRequest -Uri "$baseUrl/guides/$guideId" `
        -UseBasicParsing
    
    $guide = $response.Content | ConvertFrom-Json
    Write-Host "[OK] Guide retrieved successfully"
    Write-Host "  Name: $($guide.name)"
    Write-Host "  Email: $($guide.email)"
} catch {
    Write-Host "[FAIL] Failed to retrieve guide: $($_.Exception.Message)"
}

# Test 4: Access widget
Write-Host "`nTest 4: Accessing widget..."
try {
    $response = Invoke-WebRequest -Uri "$baseUrl/widget/$guideId" `
        -UseBasicParsing
    
    Write-Host "[OK] Widget page loaded successfully (Status: $($response.StatusCode))"
    Write-Host "  Page size: $($response.Content.Length) bytes"
} catch {
    Write-Host "[FAIL] Failed to access widget: $($_.Exception.Message)"
}

# Test 5: Access dashboard
Write-Host "`nTest 5: Accessing dashboard..."
try {
    $response = Invoke-WebRequest -Uri "$baseUrl/dashboard/$guideId" `
        -UseBasicParsing
    
    Write-Host "[OK] Dashboard page loaded successfully (Status: $($response.StatusCode))"
} catch {
    Write-Host "[FAIL] Failed to access dashboard: $($_.Exception.Message)"
}

Write-Host "`n=== Test Summary ==="
Write-Host "[OK] API is running on http://localhost:8000"
Write-Host "[OK] Visit http://localhost:8000/docs for interactive API documentation"
Write-Host "[OK] Widget URL: http://localhost:8000/widget/1"
Write-Host "[OK] Dashboard URL: http://localhost:8000/dashboard/1"
