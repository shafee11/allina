# AWS Systems Manager (SSM) Prerequisites & EC2 Setup Guide

**Target Scope:** ALLINA A7 Remediation Agent POC  
**Document ID:** `DOC-ALLINA-AWS-PREREQ-001`  

---

## 1. Overview

The ALLINA A7 Remediation Agent communicates with target EC2 instances exclusively via **AWS Systems Manager (SSM) Run Command**. 
- **No inbound SSH ports (port 22)** are opened on the EC2 security group.
- **No AWS credentials or SSH keys** are stored in the codebase or passed to models.
- Communication uses the standard AWS credential chain or named profile (`AWS_PROFILE`).

---

## 2. EC2 Instance IAM Role Prerequisites

The target EC2 instance must have an IAM instance profile attached with permissions to communicate with AWS Systems Manager.

### Step 1: Create IAM Role for EC2
1. In the AWS Management Console, navigate to **IAM** → **Roles** → **Create Role**.
2. Select **AWS service** → Use case: **EC2**.
3. Attach the AWS-managed policy:
   - `AmazonSSMManagedInstanceCore`
4. Name the role: `AllinaPocEC2SSMRole`.
5. Attach this role to your target EC2 instance (**EC2 Console** → Instance → **Actions** → **Security** → **Modify IAM role**).

---

## 3. SSM Agent Installation & Verification

The EC2 instance must have the SSM Agent installed, running, and reporting `Online`.

### Verification on Target Instance
Connect to your EC2 instance (e.g. via AWS Systems Manager Session Manager) and verify:

```bash
# On Amazon Linux 2023 / Amazon Linux 2:
sudo systemctl status amazon-ssm-agent

# On Ubuntu 22.04 / 24.04:
sudo systemctl status snap.amazon-ssm-agent.amazon-ssm-agent.service
```

Ensure the status is `active (running)`.

### Network Connectivity
The EC2 instance must be able to reach AWS Systems Manager endpoints over outbound HTTPS (port 443):
- `ssm.<region>.amazonaws.com`
- `ssmmessages.<region>.amazonaws.com`
- `ec2messages.<region>.amazonaws.com`

If the instance is in a private subnet without an Internet Gateway or NAT Gateway, configure **VPC Endpoints (PrivateLink)** for these three services.

---

## 4. Dedicated POC Directory & Test Logs Setup

For safety, ALLINA restricts remediation to a dedicated POC directory and strictly defined file prefix.

### Step 1: Create Dedicated Directory
On the target EC2 instance:
```bash
sudo mkdir -p /opt/allina/test-logs
sudo chown -R ec2-user:ec2-user /opt/allina/test-logs   # or ubuntu:ubuntu
sudo chmod 755 /opt/allina/test-logs
```

### Step 2: Generate Eligible Test Log Files
Generate sample test log files with simulated age (> 24 hours):
```bash
cd /opt/allina/test-logs

# Generate sample POC test log files
head -c 50M </dev/urandom > allina-poc-service1.log
head -c 120M </dev/urandom > allina-poc-batch-worker.log
head -c 250M </dev/urandom > allina-poc-stress-eval.log

# Set modification timestamp to 3 days ago (older than minimum 24h age)
touch -d "3 days ago" allina-poc-service1.log
touch -d "2 days ago" allina-poc-batch-worker.log
touch -d "36 hours ago" allina-poc-stress-eval.log
```

### Step 3: (Optional) Add Safeguard Test Files
Add files that ALLINA runbook safeguards must **reject**:
```bash
# 1. Fresh file (newer than 24h -> will be EXCLUDED)
echo "fresh log" > allina-poc-fresh.log

# 2. Symlink (symlink rejection -> will be EXCLUDED)
ln -s /etc/passwd allina-poc-symlink.log

# 3. Non-POC filename (prefix check -> will be EXCLUDED)
echo "production system log" > syslog.log
```

---

## 5. Local Developer AWS Configuration

On the machine running the ALLINA Teams bot:

### Configure AWS CLI Credentials
Ensure your local environment has valid AWS credentials configured:
```powershell
# Set default credentials:
aws configure

# Or using AWS IAM Identity Center (SSO):
aws sso login --profile my-sso-profile
```

### Configure `.env` in `allina-teams-bot`:
```dotenv
AWS_PROFILE=default
AWS_REGION=us-east-1
ALLINA_EC2_INSTANCE_ID=i-0123456789abcdef0
ALLINA_ALLOWED_LOG_DIR=/opt/allina/test-logs
ALLINA_ALLOWED_LOG_PREFIX=allina-poc-
ALLINA_ALLOWED_LOG_EXTENSION=.log
ALLINA_MIN_FILE_AGE_HOURS=24
ALLINA_TENANT_ID=your-microsoft-tenant-id
ALLINA_APPROVER_AAD_IDS=your-approver-entra-object-id

# Safeguards (Keep dry-run enabled during initial setup)
ALLINA_DRY_RUN=true
ALLINA_ENABLE_REAL_EXECUTION=false
ALLINA_BACKUP_CONFIRMED=false
```

---

## 6. Enabling Live Destructive Execution (Gated)

Live deletion is disabled by default. To enable controlled live deletion:

1. **Take an EBS Snapshot:** Take a snapshot of the root/data volume of the EC2 instance via the AWS Console or CLI:
   ```bash
   aws ec2 create-snapshot --volume-id vol-0123456789abcdef0 --description "Pre-remediation snapshot"
   ```
2. **Update `.env`:**
   ```dotenv
   ALLINA_DRY_RUN=false
   ALLINA_ENABLE_REAL_EXECUTION=true
   ALLINA_BACKUP_CONFIRMED=true
   ```
3. Restart the bot (`python src/main.py`).

