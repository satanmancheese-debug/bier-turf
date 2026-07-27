# Beer Stripe Tracker - Setup Guide

## What's included

- `beer-tracker/` - runs on the Chromebook (touchscreen device)
- `pc-receiver/` - runs on your main PC (backup storage)

## 1. Find your PC's local IP address

On your PC (Linux/Mac): run `ip addr` or `ifconfig` and look for something like `192.168.1.50`.
On Windows: run `ipconfig` and look for "IPv4 Address".

Both devices need to be on the same WiFi network.

## 2. Set up the PC receiver (do this first)

```bash
cd pc-receiver
python3 -m venv venv
source venv/bin/activate      # on Windows: venv\Scripts\activate
pip install -r requirements.txt
python3 receiver.py
```

This starts a server on port 6000. Check it's working by visiting
`http://localhost:6000/totals` in a browser - you should see `{}`.

Leave this running whenever you want backups to arrive. If your PC is
off or asleep, taps on the Chromebook still save locally and will
sync automatically once you turn the PC back on and the Chromebook
retries (every 30 seconds).

## 3. Configure the Chromebook app

Open `beer-tracker/app.py` and edit these two lines near the top:

```python
PC_SYNC_URL = "http://192.168.1.50:6000/receive"  # <- your PC's real IP
HOUSEMATES = ["Sanne", "Tim", "Julia", "Bram", "Noor"]  # <- your actual housemates
```

## 4. Run the Chromebook app

```bash
cd beer-tracker
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Visit `http://localhost:5000` in a browser on the Chromebook. You
should see the button grid.

## 5. Make it start automatically in kiosk mode (Ubuntu)

Create a systemd service so the Flask app always runs:

```bash
sudo nano /etc/systemd/system/beer-tracker.service
```

```ini
[Unit]
Description=Beer Stripe Tracker
After=network.target

[Service]
ExecStart=/home/<your-user>/beer-tracker/venv/bin/python3 /home/<your-user>/beer-tracker/app.py
WorkingDirectory=/home/<your-user>/beer-tracker
Restart=always
User=<your-user>

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable beer-tracker
sudo systemctl start beer-tracker
```

Then autostart Chromium in kiosk mode pointed at the page. Create:

```bash
mkdir -p ~/.config/autostart
nano ~/.config/autostart/kiosk.desktop
```

```ini
[Desktop Entry]
Type=Application
Exec=chromium-browser --kiosk --incognito --noerrdialogs --disable-infobars http://localhost:5000
X-GNOME-Autostart-enabled=true
```

Also set the Chromebook to auto-login (Settings > Users) and disable
screen sleep (Settings > Power) so it stays on and awake as a wall
display.

## 6. Check backups on the PC anytime

Visit `http://localhost:6000/totals` on the PC, or open
`pc-receiver/beers_backup.db` with any SQLite browser
(e.g. `sqlite3 beers_backup.db "SELECT * FROM beers;"`).

## Notes

- Each tap is saved locally on the Chromebook first (`beers.db`), so
  nothing is lost even if the PC or WiFi is down.
- The `/api/resync` endpoint retries any unsent taps every 30 seconds
  automatically - no manual intervention needed.
- To add/remove housemates, just edit the `HOUSEMATES` list in `app.py`
  and restart the service.
