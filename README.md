# Cloudflare - Turnstile Solver NEW

## 📢 Connect with Us

- **📢 Channel**: [https://t.me/D3_vin](https://t.me/D3_vin) - Latest updates and releases
- **💬 Chat**: [https://t.me/D3vin_chat](https://t.me/D3vin_chat) - Community support and discussions
- **📁 GitHub**: [https://github.com/D3-vin](https://github.com/D3-vin) - Source code and development

![Python](https://img.shields.io/badge/Python-3.6+-blue)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![License](https://img.shields.io/badge/License-Educational%20Use-green)


❤️ Support the Project
If you find this collection valuable and appreciate the effort involved in obtaining and sharing these insights, please consider supporting the project. Your contribution helps keep this resource updated and allows for further exploration.

You can show your support via:

Cryptocurrency:
- **EVM:** 0xeba21af63e707ce84b76a87d0ba82140048c057e  (ETH,BNB,etc)
- **TRON:** TEfECnyz5G1EkFrUqnbFcWLVdLvAgW9Raa
- **TON:** UQCJ7KC2zxV_zKwLahaHf9jxy0vsWRcvQFie_FUBJW-9LcEW
- **BTC:** bc1qdag98y5yahs6wf7rsfeh4cadsjfzmn5ngpjrcf
- **SOL:** EwXXR4VqmWSNz1sjhZ8qcQ882i4URwAwhixSPEbDzyv6
- **SUI:** 0x76da9b74c61508fbbd0b3e1989446e036b0622f252dd8d07c3fce759b239b47d


🙏 Thank you for your support!

A Python-based Turnstile solver using the patchright and camoufox libraries, featuring multi-threaded execution, API integration, and support for different browsers. It solves CAPTCHAs quickly and efficiently, with customizable configurations and detailed logging.

## 🚀 Features

- **Multi-threaded execution** - Solve multiple CAPTCHAs simultaneously
- **Multiple browser support** - Chromium, Chrome, Edge, and Camoufox
- **Proxy support** - Use proxies from proxies.txt file
- **Random browser configurations** - Rotate User-Agent and Sec-CH-UA headers
- **Detailed logging** - Comprehensive debug information
- **REST API** - Easy integration with other applications
- **Database storage** - SQLite database for result persistence
- **Automatic cleanup** - Old results are automatically cleaned up
- **Image blocking** - Optimized performance by blocking unnecessary images

## 🔧 Configuration

### Browser Configurations

The solver supports various browser configurations with realistic User-Agent strings and Sec-CH-UA headers:

- **Chrome** (versions 136-139)
- **Edge** (versions 137-139)
- **Avast** (versions 137-138)
- **Brave** (versions 137-139)

### Proxy Format

Add proxies to `proxies.txt` in the following formats:

```
ip:port
ip:port:username:password
scheme://ip:port
scheme://username:password@ip:port
```

## ❗ Disclaimers

I am not responsible for anything that may happen, such as API Blocking, IP ban, etc.  
This was a quick project that was made for fun and personal use if you want to see further updates, star the repo & create an "issue" here

## ⚙️ Installation Instructions

Ensure Python 3.8+ is installed on your system.

### 1. Create a Python virtual environment:

```bash
python -m venv venv
```

### 2. Activate the virtual environment:

**On Windows:**
```bash
venv\Scripts\activate
```

**On macOS/Linux:**
```bash
source venv/bin/activate
```

### 3. Install required dependencies:

```bash
pip install -r requirements.txt
```

### 4. Select the browser to install:

You can choose between Chromium, Chrome, Edge or Camoufox:

**To install Chromium:**
```bash
python -m patchright install chromium
```

**To install Chrome:**
- **On macOS/Windows:** [Click here](https://www.google.com/chrome/)
- **On Linux (Debian/Ubuntu-based):**
```bash
apt update
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
apt install -y ./google-chrome-stable_current_amd64.deb
apt -f install -y  # Fix dependencies if needed
rm ./google-chrome-stable_current_amd64.deb
```

**To install Edge:**
```bash
python -m patchright install msedge
```

**To install Camoufox:**
```bash
python -m camoufox fetch
```

### 5. Start testing:

Run the script (Check [🔧 Command line arguments](#-command-line-arguments) for better setup):

```bash
python api_solver.py
```

## 🔧 Command line arguments

| Parameter | Default | Type | Description |
|-----------|---------|------|-------------|
| `--no-headless` | False | boolean | Runs the browser with GUI (disable headless mode). By default, headless mode is enabled. |
| `--useragent` | None | string | Specifies a custom User-Agent string for the browser. (No need to set if camoufox used) |
| `--debug` | False | boolean | Enables or disables debug mode for additional logging and troubleshooting. |
| `--browser_type` | chromium | string | Specify the browser type for the solver. Supported options: chromium, chrome, msedge, camoufox |
| `--thread` | 4 | integer | Sets the number of browser threads to use in multi-threaded mode. |
| `--host` | 0.0.0.0 | string | Specifies the IP address the API solver runs on. |
| `--port` | 6080 | integer | Sets the port the API solver listens on. |
| `--proxy` | False | boolean | Select a random proxy from proxies.txt for solving captchas |
| `--random` | False | boolean | Use random User-Agent and Sec-CH-UA configuration from pool |
| `--browser` | None | string | Specify browser name to use (e.g., chrome, firefox) |
| `--version` | None | string | Specify browser version to use (e.g., 139, 141) |

## 📡 API Documentation

### Solve turnstile

```
GET /turnstile?url=https://example.com
```

**Request Parameters:**

| Parameter | Type | Description | Required |
|-----------|------|-------------|----------|
| `url` | string | The target URL containing the CAPTCHA. (e.g., https://example.com) | Yes |
| `sitekey` | string | The site key for the CAPTCHA to be solved. If omitted, the service will try to auto-detect it from the target page. | No |
| `action` | string | Action to trigger during CAPTCHA solving, e.g., login | No |
| `cdata` | string | Custom data that can be used for additional CAPTCHA parameters. | No |

**Response:**

If the request is successfully received, the server will respond with a task_id for the CAPTCHA solving task:

```json
{
  "task_id": "d2cbb257-9c37-4f9c-9bc7-1eaee72d96a8"
}
```

### Get Result

```
GET /result?id=f0dbe75b-fa76-41ad-89aa-4d3a392040af
```

**Request Parameters:**

| Parameter | Type | Description | Required |
|-----------|------|-------------|----------|
| `id` | string | The unique task ID returned from the /turnstile request. | Yes |

**Response:**

If the CAPTCHA is solved successfully, the server will respond with the following information:

```json
{
  "status": "ready",
  "value": "0.KBtT-r",
  "elapsed_time": 7.625
}
```

**Error Responses:**

```json
{
  "status": "processing"
}
```

```json
{
  "status": "fail",
  "value": "CAPTCHA_FAIL",
  "elapsed_time": 30.0
}
```



## 🐛 Troubleshooting

### Common Issues

1. **Browser not found**: Make sure you've installed the required browser using the installation instructions
2. **Permission denied**: Run with appropriate permissions or check file permissions
3. **Port already in use**: Change the port using `--port` argument
4. **Proxy connection failed**: Check proxy format and availability

### Debug Mode

Enable debug mode for detailed logging:

```bash
python api_solver.py --debug
```

## 📊 Performance

- **Average solving time**: 5-15 seconds
- **Success rate**: 95%+ (depending on site complexity)
- **Memory usage**: ~50-100MB per browser thread
- **CPU usage**: Moderate (depends on thread count)

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📄 License

This project is for educational purposes only. Use at your own risk.

