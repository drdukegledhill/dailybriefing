#!/usr/bin/env bash
# Set up The Morning Brief on a Raspberry Pi (Raspberry Pi OS, 64-bit).
# Run from the project folder on the Pi:   ./pi/install.sh
# With an Inky pHAT status screen:         ./pi/install.sh --inky
# Safe to run again after updating the code.
set -euo pipefail

cd "$(dirname "$0")/.."
DIR="$(pwd)"
RUN_AS="$(id -un)"
WHEN="${MORNING_BRIEF_TIME:-Mon..Fri *-*-* 06:15:00}"
INKY=no
[ "${1:-}" = "--inky" ] && INKY=yes
REBOOT=no

echo "==> Installing system packages (needs sudo)"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip \
  libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 fonts-dejavu-core

echo "==> Creating the Python environment"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> Created .env from .env.example: add your ANTHROPIC_API_KEY before the first run"
fi
chmod +x brief.sh

if [ "$INKY" = yes ]; then
  echo "==> Setting up the Inky pHAT"
  .venv/bin/pip install -q "inky>=2.0"
  sudo raspi-config nonint do_spi 0
  sudo raspi-config nonint do_i2c 0
  BOOT=/boot/firmware/config.txt; [ -f "$BOOT" ] || BOOT=/boot/config.txt
  # The Inky library drives the chip-select pin itself, so free it from the SPI driver.
  if ! grep -q "^dtoverlay=spi0-0cs" "$BOOT"; then
    echo "dtoverlay=spi0-0cs" | sudo tee -a "$BOOT" >/dev/null
    REBOOT=yes
  fi
  sudo usermod -aG spi,i2c,gpio "$RUN_AS"
  if grep -q "^INKY=" .env; then
    sed -i 's/^INKY=.*/INKY=true/' .env
  else
    printf '\n# Inky pHAT status screen\nINKY=true\n' >> .env
  fi
fi

echo "==> Installing the daily timer ($WHEN)"
sudo tee /etc/systemd/system/morning-brief.service >/dev/null <<EOF
[Unit]
Description=The Morning Brief: make today's briefing
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=$RUN_AS
WorkingDirectory=$DIR
ExecStart=$DIR/brief.sh run
TimeoutStartSec=30min
EOF

sudo tee /etc/systemd/system/morning-brief.timer >/dev/null <<EOF
[Unit]
Description=The Morning Brief: weekday mornings

[Timer]
OnCalendar=$WHEN
# If the Pi was off at that time, run as soon as it's back on.
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now morning-brief.timer

echo
echo "Done. Time zone: $(timedatectl show -p Timezone --value) (should be Europe/London;"
echo "  fix with: sudo timedatectl set-timezone Europe/London)"
systemctl list-timers morning-brief.timer --no-pager | head -2
echo
if [ "$INKY" = yes ]; then
  echo "Test the screen: ./brief.sh screen --sample"
  [ "$REBOOT" = yes ] && echo ">>> Reboot first so the screen works: sudo reboot"
fi
echo "Try it now:      ./brief.sh run --preview"
echo "Run for real:    sudo systemctl start morning-brief"
echo "See the log:     journalctl -u morning-brief -n 50"
