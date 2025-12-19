# ASAT Order Validator - English Version

## 🎯 Overview

Complete validation system for ASAT orders with hierarchical business logic, automatic logging, and interactive resend functionality.

## ✨ Key Features

✅ **Hierarchical Validation** - Follows exact business rules
✅ **V041 Complete Handling** - Verifies multiple orders, credit memo
✅ **Automatic Logging** - Saves all actions to files
✅ **Parallel Processing** - Fast with configurable workers
✅ **Interactive Resend** - Review before resending
✅ **Detailed Reports** - JSONL + logs for analysis

---

## 🚀 Quick Start

```powershell
# Set credentials
$env:WPP_AUTH_USER = "your.email@wiley.com"
$env:WPP_AUTH_PASS = "your_password"

# Run validation
python order_validator_en.py --verbose

# Files generated:
# - output/validation_log_YYYYMMDD_HHMMSS.log
# - output/validation_results_YYYYMMDD_HHMMSS.jsonl
```

---

## 📋 Input Format

CSV file with header:
```csv
ORDER_UNIQUE_ID
10000136242
10000136243
```

Place files in `./input/` folder (or use `--input-dir`)

---

## ⚙️ Command Line Options

```powershell
python order_validator_en.py [OPTIONS]

Options:
  --input-dir DIR         Input directory (default: ./input)
  --output-dir DIR        Output directory (default: ./output)
  --output FILE           JSONL output filename
  --auth-user USER        WPP username (or use WPP_AUTH_USER env var)
  --auth-pass PASS        WPP password (or use WPP_AUTH_PASS env var)
  --max-workers N         Parallel workers (default: 10)
  --timeout SECONDS       Request timeout (default: 10.0)
  --verbose, -v           Verbose output (DEBUG level)
  --no-interactive        Skip interactive resend menu
```

---

## 🔍 Validation Logic

### Step 1: Check Cancellation
```
IF orderStatus == "OrderCanceledInAMP":
    ❌ BLOCK - do not resend
    STOP
```

### Step 2: Check Errors
```
IF no errors:
    → Go to Step 4 (Revenue Model)

IF error detected:
    IF error_code == "V041":
        → Continue to Step 3
    ELSE:
        ❌ BLOCK - other error
        STOP
```

### Step 3: V041 Complete Validation
```
a) GET multiple orders for article
b) Count non-canceled orders (excluding current)
   IF count > 0:
       ❌ BLOCK - "Multiple active orders - requires VIAX review"
       STOP

c) Find canceled orders, check for credit memo
   IF no credit memo:
       ❌ BLOCK - "Canceled order without credit memo - requires VIAX review"
       STOP

   IF credit memo found:
       ✅ V041 RESOLVED
       → Continue to Step 4
```

### Step 4: Revenue Model Validation
```
IF revenueModel == "OO":
    IF totalChargedAmount == 0:
        ❌ BLOCK
    ELSE:
        ✅ APPROVE

IF revenueModel == "OA":
    IF paymentMethod == "Invoice":
        IF totalChargedAmount == 0:
            ❌ BLOCK
        ELSE:
            ✅ APPROVE
    ELSE:
        ✅ APPROVE (regardless of amount)
```

---

## 📊 Output Files

### 1. Validation Log
```
output/validation_log_20251219_153045.log
```
- Complete audit trail
- Every action timestamped
- DEBUG level details
- For troubleshooting

### 2. JSONL Results
```
output/validation_results_20251219_153045.jsonl
```
- Structured data
- One JSON object per line
- For programmatic analysis

Example:
```json
{
  "order_id": "10000136242",
  "order_status": "OrderCreationInAMPFailed",
  "has_error": true,
  "error_code": "V041",
  "is_v041_error": true,
  "other_orders_not_canceled": 0,
  "canceled_order_has_credit_memo": true,
  "revenue_model": "GOA",
  "payment_method": "WAIVED",
  "total_charged": 0.0,
  "can_resend": true,
  "validation_reason": "✅ CAN RESEND: V041 resolved + GOA + WAIVED"
}
```

---

## 🎮 Interactive Resend

After validation, the script shows approved orders and asks:

```
✅ ORDERS APPROVED FOR RESEND
================================================================================

1. Order ID: 10000136242
   Status: OrderCreationInAMPFailed
   Reason: ✅ CAN RESEND: V041 resolved + GOA + WAIVED
   Revenue Model: GOA
   Payment: WAIVED ($0.0)
   ⚠️ V041 resolved (credit memo found)

================================================================================
Total approved: 1
================================================================================

Resend orders? (y/n/list):
```

Options:
- `y` or `yes` - Resend all approved
- `n` or `no` - Skip resend
- `list` - Show list again
- `1,3,5` - Resend specific orders by number
- `q` or `quit` - Exit

---

## 🔧 Configuration

### Environment Variables
```powershell
# Recommended: use environment variables
$env:WPP_AUTH_USER = "your.email@wiley.com"
$env:WPP_AUTH_PASS = "your_password"

python order_validator_en.py
```

### Command Line
```powershell
# Alternative: pass credentials directly
python order_validator_en.py `
    --auth-user your.email@wiley.com `
    --auth-pass "your_password"
```

### Custom Directories
```powershell
python order_validator_en.py `
    --input-dir "C:\data\orders" `
    --output-dir "C:\data\results"
```

### Performance Tuning
```powershell
# More workers for faster processing
python order_validator_en.py --max-workers 20

# Increase timeout for slow connections
python order_validator_en.py --timeout 30
```

---

## 📈 Statistics and Reporting

The log automatically includes:

### Validation Summary
```
📊 VALIDATION SUMMARY
Total orders: 50
✅ Approved for resend: 35
⚠️  Blocked by rules: 15
```

### Grouped Blocking Reasons
```
📋 BLOCKING REASONS:
  • 15x: ❌ BLOCKED: OO with totalChargedAmount = 0
  • 10x: ❌ BLOCKED: OA + Invoice with totalChargedAmount = 0
  •  8x: ❌ BLOCKED: V041: multiple active orders - requires VIAX review
  •  5x: ❌ BLOCKED: Order canceled
```

### Resend Results
```
📊 RESEND SUMMARY
✅ Successes: 33/35
❌ Failures: 2/35

Orders with failures:
  • 10000136248: Order already completed
  • 10000136250: Invalid order status
```

---

## 🐛 Troubleshooting

### No CSV Files Found
- Check `--input-dir` path
- Ensure CSV has `ORDER_UNIQUE_ID` column
- Verify file encoding (UTF-8)

### Authentication Failed
- Verify credentials
- Check network connectivity
- Ensure VPN is active if required

### Order Blocked - Why?
- Check validation log
- Search for order ID
- See step-by-step validation
- Review `validation_reason` field

### V041 Always Blocked
- Verify endpoint is correct
- Check if credit memo exists
- Review multiple orders response

---

## 📚 Documentation Files

- `README_EN.md` - This file
- `LOGS_DOCUMENTATION_EN.md` - Complete logging guide
- `BUG_FIX_DOCUMENTATION_EN.md` - Bug fix details
- `TEST_GUIDE_EN.md` - Testing instructions

---

## ✅ Validation Decision Matrix

| Canceled? | Error? | Type | Other Active | Credit Memo | RM | Payment | Amount | RESULT |
|-----------|--------|------|--------------|-------------|----|---------|---------|---------|
| YES | - | - | - | - | - | - | - | ❌ BLOCK |
| NO | NO | - | - | - | OO | - | 0 | ❌ BLOCK |
| NO | NO | - | - | - | OO | - | >0 | ✅ APPROVE |
| NO | NO | - | - | - | OA | Invoice | 0 | ❌ BLOCK |
| NO | NO | - | - | - | OA | Invoice | >0 | ✅ APPROVE |
| NO | NO | - | - | - | OA | Other | any | ✅ APPROVE |
| NO | YES | V041 | >0 | - | - | - | - | ❌ VIAX |
| NO | YES | V041 | 0 | NO | - | - | - | ❌ VIAX |
| NO | YES | V041 | 0 | YES | OO | - | 0 | ❌ BLOCK |
| NO | YES | V041 | 0 | YES | OO | - | >0 | ✅ APPROVE |
| NO | YES | V041 | 0 | YES | OA | Invoice | 0 | ❌ BLOCK |
| NO | YES | V041 | 0 | YES | OA | Invoice | >0 | ✅ APPROVE |
| NO | YES | V041 | 0 | YES | OA | Other | any | ✅ APPROVE |
| NO | YES | Other | - | - | - | - | - | ❌ BLOCK |

---

## 🎯 Examples

### Basic Usage
```powershell
python order_validator_en.py
```

### With Verbose Logging
```powershell
python order_validator_en.py --verbose
```

### Non-Interactive Mode
```powershell
python order_validator_en.py --no-interactive
```

### Custom Configuration
```powershell
python order_validator_en.py `
    --input-dir ./data/input `
    --output-dir ./data/output `
    --output results_dec2025.jsonl `
    --max-workers 15 `
    --verbose
```

---

## 🔐 Security

- Never commit credentials to version control
- Use environment variables for credentials
- Rotate passwords regularly
- Logs may contain order IDs - handle appropriately

---

## 📞 Support

For issues or questions:
1. Check logs in `output/` directory
2. Review `LOGS_DOCUMENTATION_EN.md`
3. Search for order ID in validation log
4. Check `BUG_FIX_DOCUMENTATION_EN.md` for known issues

---

## 🎉 Features Summary

✅ Complete V041 validation with multiple orders check
✅ Credit memo verification
✅ Revenue model rules (OO/OA)
✅ Invoice + amount validation
✅ Parallel processing
✅ Automatic detailed logging
✅ Interactive resend
✅ VIAX case detection
✅ Grouped statistics
✅ Error handling

**All business rules correctly implemented!** 🚀