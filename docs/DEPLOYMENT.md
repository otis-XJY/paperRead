# Deployment Guide

This guide covers different deployment options for Zotero AI Daily Papers.

## Table of Contents

- [Local Deployment](#local-deployment)
- [Docker Deployment](#docker-deployment)
- [GitHub Actions](#github-actions)
- [VPS Deployment](#vps-deployment)
- [Cloud Deployment](#cloud-deployment)

## Local Deployment

### Requirements

- Python 3.8+
- pip

### Steps

1. Clone the repository
```bash
git clone https://github.com/your-username/paperRead.git
cd paperRead
```

2. Install dependencies
```bash
pip install -r requirements.txt
```

3. Configure environment variables
```bash
cp .env.example .env
# Edit .env with your configuration
```

4. Build knowledge base (optional)
```bash
python zotero_indexer.py
```

5. Run the application
```bash
python main.py
```

### Setting up Scheduled Runs

#### Using cron (Linux/macOS)

Edit crontab:
```bash
crontab -e
```

Add a daily job at 9 AM:
```
0 9 * * * cd /path/to/paperRead && python main.py >> paperRead.log 2>&1
```

#### Using Task Scheduler (Windows)

1. Open Task Scheduler
2. Create Basic Task
3. Set trigger to "Daily" at 9:00 AM
4. Action: Start a program
5. Program: `python.exe`
6. Arguments: `C:\path\to\paperRead\main.py`

## Docker Deployment

### Dockerfile

Create a `Dockerfile`:

```dockerfile
FROM python:3.10-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY *.py ./

# Create directory for data
RUN mkdir -p /app/data

# Set environment variables
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
```

### docker-compose.yml

```yaml
version: '3.8'

services:
  paperread:
    build: .
    container_name: zotero-paper-read
    restart: unless-stopped
    environment:
      - ZOTERO_USER_ID=${ZOTERO_USER_ID}
      - ZOTERO_API_KEY=${ZOTERO_API_KEY}
      - MODELSCOPE_API_KEY=${MODELSCOPE_API_KEY}
      - FEISHU_WEBHOOK_URL=${FEISHU_WEBHOOK_URL}
      - ENABLE_NOTIFICATION=${ENABLE_NOTIFICATION:-1}
    volumes:
      - ./data:/app/data
      - ./.env:/app/.env:ro
```

### Build and Run

```bash
# Build image
docker build -t zotero-paper-read .

# Run container
docker run -d \
  --name zotero-paper-read \
  --env-file .env \
  zotero-paper-read

# Or using docker-compose
docker-compose up -d
```

## GitHub Actions

### Automatic Daily Runs

The `.github/workflows/daily.yml` file is already configured for daily runs.

### Setup

1. Go to repository Settings → Secrets and variables → Actions
2. Add the following secrets:
   - `ZOTERO_USER_ID`
   - `ZOTERO_API_KEY`
   - `MODELSCOPE_API_KEY`
   - `FEISHU_WEBHOOK_URL` (optional)

3. Enable Actions in repository settings
4. The workflow will run automatically at 9:00 UTC daily

### Manual Trigger

You can also trigger the workflow manually:
- Go to Actions tab
- Select "Daily Paper Fetch" workflow
- Click "Run workflow"

## VPS Deployment

### Prerequisites

- Ubuntu/Debian server
- SSH access
- Python 3.8+

### Steps

1. SSH into your server
```bash
ssh user@your-server.com
```

2. Clone repository
```bash
cd /opt
git clone https://github.com/your-username/paperRead.git
cd paperRead
```

3. Create virtual environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

4. Configure
```bash
cp .env.example .env
nano .env  # Edit configuration
```

5. Build knowledge base
```bash
python zotero_indexer.py
```

6. Test run
```bash
python main.py
```

7. Set up cron job
```bash
crontab -e
# Add: 0 9 * * * cd /opt/paperRead && source venv/bin/activate && python main.py >> logs/paperRead.log 2>&1
```

### Using systemd (Recommended)

Create service file: `/etc/systemd/system/paperread.service`

```ini
[Unit]
Description=Zotero AI Daily Papers
After=network.target

[Service]
Type=simple
User=your-username
WorkingDirectory=/opt/paperRead
Environment="PATH=/opt/paperRead/venv/bin"
ExecStart=/opt/paperRead/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable paperread
sudo systemctl start paperread
sudo systemctl status paperread
```

## Cloud Deployment

### Tencent Cloud Base (云开发)

适合微信小程序和Web应用场景。

1. **创建云开发环境**
   - 登录腾讯云控制台
   - 创建云开发环境

2. **配置云函数**
   - 创建云函数（Python 3.8+）
   - 上传代码和依赖
   - 配置环境变量

3. **设置定时触发器**
   - 在云函数设置中配置定时触发
   - 设置每天9:00触发

### AWS Lambda

```python
import json
import os

def lambda_handler(event, context):
    # Your main.py logic here
    from main import main
    import asyncio
    
    # Run the async main function
    asyncio.run(main())
    
    return {
        'statusCode': 200,
        'body': json.dumps('Paper fetch completed')
    }
```

### Google Cloud Functions

Similar setup to AWS Lambda, using Python runtime.

## Monitoring

### Logs

Check logs to ensure proper operation:

```bash
# Local/VPS
tail -f paperRead.log

# Docker
docker logs -f zotero-paper-read

# systemd
sudo journalctl -u paperread -f
```

### Health Checks

Create a simple health check endpoint if needed.

## Backup

Important files to backup:

```bash
# Backup state files
tar -czf paperread-backup-$(date +%Y%m%d).tar.gz \
    state.json \
    history.json \
    knowledge_base.json
```

## Troubleshooting

### Common Issues

1. **Permission Denied**
   - Check file permissions
   - Ensure Python executable is executable

2. **Module Not Found**
   - Verify all dependencies are installed
   - Check Python path

3. **API Rate Limits**
   - Implement backoff strategies
   - Monitor API usage

4. **Memory Issues**
   - Increase system memory
   - Process papers in batches

## Security Considerations

- Never commit `.env` files
- Rotate API keys regularly
- Use HTTPS for all API calls
- Implement rate limiting
- Monitor logs for suspicious activity

## Support

For issues specific to deployment, please open a GitHub issue with:
- Deployment method used
- Error logs
- System information
