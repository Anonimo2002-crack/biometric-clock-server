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

Editar `.env`: `DATABASE_URL` de MySQL, IP del reloj y la contraseña `admin` del aparato. **No subas el `.env` a GitHub.**

```powershell
$env:PATH = "$(Get-Location)\venv\Scripts;" + $env:PATH
python -m prisma generate
python -m prisma db push
python -m uvicorn biometric-clock-server:app --host 0.0.0.0 --port 8000
```

Swagger: http://127.0.0.1:8000/docs

## MySQL (PC de la escuela / Debian)

La fuente de las tablas es `schema.prisma` (`Persona`, `DetalleAlumno`, `DetalleCatedratico`, `Grado`, `Dispositivo`, `Asistencia`, `Usuario`). No hay script SQL aparte: crea la base `asistencia_db` y corre `python -m prisma db push`.

El `rol` de `Persona` es el del reloj (`ALUMNO`, `CATEDRATICO`, `ADMIN`). Las cuentas del tablero van en `Usuario` (login JWT). El Hikvision guarda las caras y huellas aparte.

En `.env`:

```
DATABASE_URL=mysql://USUARIO:CLAVE@127.0.0.1:3306/asistencia_db
```


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

## Alumnos (CUI y emergencia)

Alta: `POST /api/alumnos`. Identificador único de alumno: **CUI** (13 dígitos). El `employeeNo` del reloj lo asigna la API.

Contacto de emergencia: `contactoEmergenciaNombre`, `contactoEmergenciaParentesco`, `contactoEmergenciaTelefono`.

Grados del catálogo `GET /api/catalogos/grados`: P1A, P2A, P2B, P3A, P3B, 1A, 1B, 2A, 3A, 4A, 4B, 5A, 5B, 6A (párvulos + primaria según MINEDUC 2026).


## Reloj Hikvision

- `POST /api/asistencia/sincronizar` baja marcajes por ISAPI y los guarda.
- `DEVICE_IP` cambia en la red de la escuela.

## Roles

- **Jarod:** esta API + comunicación con el biométrico.
- **Geovany:** interfaz Angular, nginx y montaje en Debian.
