# Deploy en Raspberry Pi

## 1. Preparar el hardware (desde la Mac)

1. Actualizar el bootloader del Pi 5 desde una microSD temporal usando Raspberry Pi Imager — sin este paso el boot desde NVMe puede fallar.
2. Grabar Raspberry Pi OS Lite 64-bit en el NVMe con SSH y WiFi preconfigurados desde el Imager. Sin monitor ni teclado.
3. Ensamblar Pi 5 + M.2 HAT+ + SSD + case y conectar.

## 2. Primera conexión

Asignar IP fija al Pi via DHCP reservation en el router antes de conectar.

```bash
ssh YOUR_USERNAME@<ip>
```

Instalar dependencias y clonar el repo:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
cd ~
git clone https://github.com/juanroman/spotify-liked-sync.git
cd spotify-liked-sync && uv sync
```

## 3. OAuth (desde la Mac — requiere browser)

```bash
# En la Mac:
uv run python -m sync auth

# Copiar tokens a la Pi:
scp ~/.local/share/spotify-sync/tokens.json YOUR_USERNAME@<ip>:/home/YOUR_USERNAME/.local/share/spotify-sync/
```

## 4. Configurar config.toml en la Pi

Crear `/home/YOUR_USERNAME/spotify-liked-sync/config.toml`:

```toml
[spotify]
client_id = "..."
client_secret = "..."

[notifications]
pushover_token = "..."
pushover_user = "..."
```

## 5. Instalar el systemd timer

Crear `/etc/systemd/system/spotify-sync.service`:

```ini
[Unit]
Description=Spotify Liked Sync
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/spotify-liked-sync
ExecStart=/home/YOUR_USERNAME/.local/bin/uv run python -m sync run
StandardOutput=journal
StandardError=journal
```

Crear `/etc/systemd/system/spotify-sync.timer`:

```ini
[Unit]
Description=Run Spotify Sync every 15 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
```

Habilitar:

```bash
sudo systemctl enable --now spotify-sync.timer
```

## 6. Validar

```bash
# Run manual:
uv run python -m sync run

# Logs del timer:
journalctl -u spotify-sync.service -f
```

## Re-autenticación (token expirado)

Desde julio 2026, Spotify expira los refresh tokens a los ~6 meses. Cuando esto ocurra, el sync fallará con un error `invalid_grant` y recibirás un push a tu iPhone con el título **"Spotify Sync — Re-auth Required"**. El log de systemd también mostrará el mensaje de error con instrucciones.

### Pasos para re-autenticar

1. **En la Mac** (requiere browser):

   ```bash
   uv run python -m sync auth
   ```

2. **Copiar los tokens a la Pi**:

   ```bash
   scp ~/.local/share/spotify-sync/tokens.json YOUR_USERNAME@<PI_IP>:/home/YOUR_USERNAME/.local/share/spotify-sync/
   ```

3. **Verificar que el timer sigue activo**:

   ```bash
   ssh YOUR_USERNAME@<PI_IP> "systemctl --user status spotify-sync.timer"
   # Si está parado: systemctl --user start spotify-sync.timer
   ```

El sync debería recuperarse automáticamente en el siguiente ciclo (15 minutos).

> **Aviso proactivo:** Si se configura `authorized_at` en `tokens.json` (automático desde la versión que incluye esta sección), el sync loguea un WARNING y envía un push cuando quedan ≤14 días para la expiración — así puedes re-autenticar antes de que el Pi se quede sin acceso.

## Actualizaciones futuras

```bash
ssh YOUR_USERNAME@<ip> "cd spotify-liked-sync && git pull && uv sync"
```
