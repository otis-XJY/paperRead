# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x  | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly.

### How to Report

1. **Do not** create a public issue
2. Send an email to: your-email@example.com
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if known)

### What to Expect

- We will respond within 48 hours
- We will work to understand and fix the issue
- We will coordinate with you on disclosure timeline
- We will credit you in the security advisory

### Security Best Practices for Users

1. **Never commit API keys** to version control
2. **Use environment variables** for sensitive configuration
3. **Rotate API keys** regularly
4. **Keep dependencies updated**: `pip install --upgrade -r requirements.txt`
5. **Review GitHub Actions** configuration before enabling
6. **Use .env files** and add to `.gitignore`

### Known Security Considerations

- API keys are stored in environment variables only
- No data is transmitted to third-party services except configured APIs
- Zotero API credentials should have minimal required permissions
- LLM API calls may transmit paper titles and abstracts for analysis

### Dependency Updates

We regularly update dependencies for security patches. Monitor releases for security updates.
