-- EORM Agua de la Mina — Jornada Vespertina
-- Tablas equivalentes al schema.prisma (producción en Debian/MySQL).
-- En GitHub NO va una base llena: solo este diseño. Los datos se crean en la PC de la escuela.

CREATE DATABASE IF NOT EXISTS asistencia_db
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE asistencia_db;

-- Alumnos, catedráticos y el admin del reloj (campo rol).
-- No hay tabla "usuarios" de login web: el tablero consume la API; el Hikvision tiene sus propios usuarios.
CREATE TABLE IF NOT EXISTS alumnos (
  id INT NOT NULL AUTO_INCREMENT,
  nombre VARCHAR(191) NOT NULL,
  codigo VARCHAR(191) NOT NULL,
  employeeNo VARCHAR(191) NOT NULL,
  grado VARCHAR(191) NULL COMMENT '1A..6A según catálogo del tablero',
  cargo VARCHAR(191) NULL COMMENT 'Solo catedráticos, ej. Docente 1ro A',
  rol VARCHAR(191) NOT NULL DEFAULT 'ALUMNO' COMMENT 'ALUMNO | CATEDRATICO | ADMIN',
  activo TINYINT(1) NOT NULL DEFAULT 1,
  creadoEn DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY alumnos_codigo_key (codigo),
  UNIQUE KEY alumnos_employeeNo_key (employeeNo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS asistencias (
  id INT NOT NULL AUTO_INCREMENT,
  alumnoId INT NOT NULL,
  fechaHora DATETIME(3) NOT NULL,
  tipo VARCHAR(191) NOT NULL COMMENT 'ENTRADA | SALIDA',
  metodo VARCHAR(191) NOT NULL DEFAULT 'ROSTRO',
  serialEvento VARCHAR(191) NULL COMMENT 'evita duplicar el mismo marcaje del reloj',
  dispositivoIp VARCHAR(191) NULL,
  creadoEn DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY asistencias_serialEvento_key (serialEvento),
  KEY asistencias_fechaHora_idx (fechaHora),
  KEY asistencias_alumnoId_fechaHora_idx (alumnoId, fechaHora),
  CONSTRAINT asistencias_alumnoId_fkey
    FOREIGN KEY (alumnoId) REFERENCES alumnos (id)
    ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
