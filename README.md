# API Reloj Biométrico — EORM Agua de la Mina

Backend **FastAPI** + **Prisma** + **ISAPI Hikvision** para el control de asistencia de la jornada vespertina.

El tablero Angular de Geovany consume este API (`proxy` a `http://localhost:8000`):
https://github.com/Geovany-Gonzalez/biometric-clock-client

## Cómo correrlo

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Edita `.env`: IP del reloj y la contraseña `admin` del aparato. **No subas `.env` a GitHub.**

```powershell
$env:PATH = "$(Get-Location)\venv\Scripts;" + $env:PATH
python -m prisma generate
python -m prisma db push
python -m uvicorn biometric-clock-server:app --host 0.0.0.0 --port 8000
```

Swagger: http://127.0.0.1:8000/docs

## Contrato (tablero Angular)

| Método | Ruta | Query |
| --- | --- | --- |
| GET | `/api/dashboard` | `fecha` |
| GET | `/api/reportes/asistencia-grados` | `fecha`, `gradoId` |
| GET | `/api/reportes/ausencias` | `fecha`, `horaCorte` |
| GET | `/api/reportes/maestros` | `fecha` |
| GET | `/api/catalogos/grados` | — |

Estados: `presente` | `tarde` | `ausente`. Tarde alumnos: después de 13:15. Tarde maestros: después de 12:55.

En el client: `src/environments/environment.ts` → `useMocks: false`.

## Reloj Hikvision

- `POST /api/asistencia/sincronizar` baja marcajes por ISAPI y los guarda.
- `DEVICE_IP` cambia en la red de la escuela.

## Roles

- **Jarod:** esta API + comunicación con el biométrico.
- **Geovany:** interfaz Angular, nginx y montaje en Debian.
