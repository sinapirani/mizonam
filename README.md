# mizonam (میزونم)

**Asymmetric Upload Balancer for Linux servers**

Internet providers and some filtering systems monitor the upload/download ratio of a server's traffic. A ratio too close to `1:1` can look suspicious for a relay/proxy server. `mizonam` runs as a background service and keeps your upload traffic proportional to your download, making the ratio look natural.

- **Zero dependencies** — pure Python 3 stdlib, no `pip install` needed
- **Runs as a systemd service** — starts automatically on boot, restarts on failure
- **Interactive menu** — live stats, easy config, no config file editing needed
- **Time-aware** — adjusts thread count based on Tehran time (peak/off-peak hours)
- **MTU auto-adjust** — detects and handles MTU limits automatically

---

## Install

### 🇮🇷 Inside Iran

```bash
bash <(curl -fsSL https://sinapiranidl.storage.iran.liara.space/mizonam/mizonam-installer.sh)
```

### 🌍 Outside Iran

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/sinapirani/mizonam/refs/heads/main/mizonam-installer.sh)
```

> **Requires:** Ubuntu 18.04+ · Python 3.6+ · root (`sudo`)

---

## Usage

```bash
mizonam menu      # interactive dashboard (recommended)
mizonam status    # quick status check
mizonam start     # start service
mizonam stop      # stop service
mizonam restart   # restart service
```

---

## How it works

`mizonam` reads your server's cumulative upload/download counters from `/proc/net/dev`. When the upload lags behind `download × coefficient`, it sends UDP packets to random IPs within Iranian address ranges to close the gap. All traffic stays within valid IP ranges (RIPE NCC data).

---

## Configuration

All settings are available inside `mizonam menu`. No manual file editing required.

| Setting | Default | Description |
|---|---|---|
| Coefficient | `3` | Target upload/download ratio |
| Threads | `5` | Base worker threads |
| Buffer | `16 KB` | UDP payload size (auto-adjusted) |
| Interface | `auto` | Network interface |
| Debug mode | `on` | Log sample IPs and errors |
| Custom CIDRs | — | Add your own target IP ranges |

Config is saved to `/etc/mizonam/config.json`.

---

## Requirements

- Ubuntu 18.04 or later
- Python 3.6 or later *(pre-installed on Ubuntu 18.04+)*
- Root access

**No pip packages. No external libraries. Nothing to install beyond the script itself.**

---

## License

MIT
