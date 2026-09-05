import pymysql

cn = pymysql.connect(
    host="127.0.0.1",
    user="root",
    password="admin98",
    database="asistencia_db",
    charset="utf8mb4",
)
c = cn.cursor()
c.execute("SELECT COUNT(*) FROM personas WHERE rol=%s AND activo=1", ("CATEDRATICO",))
print("maestros activos", c.fetchone()[0])
c.execute(
    "SELECT COUNT(*) FROM alumnos_detalle d JOIN personas p ON p.id=d.personaId "
    "WHERE d.gradoId='P3A' AND CHAR_LENGTH(d.telefonoPadres)=8"
)
print("P3A con telefono 8 digitos", c.fetchone()[0])
c.execute("SELECT activo, COUNT(*) FROM personas GROUP BY activo")
print("personas por activo", c.fetchall())
c.close()
cn.close()
