# 🗺️ Mapa de Riesgo — Conflicto Armado Colombia
### Convoy of Hope · Confidencial · Solo Personal Autorizado

Mapa interactivo de riesgo de seguridad a nivel municipal para las operaciones humanitarias de Convoy of Hope en Colombia. Se actualiza automáticamente cada **domingo a las 23:00 hora de Colombia (COT)** usando inteligencia de conflicto en tiempo real vía la API de Anthropic.

---

## 📋 Instrucciones de Configuración (una sola vez)

### Paso 1 — Clonar o descargar este repositorio

```bash
git clone https://github.com/SU_USUARIO/colombia-risk-map.git
cd colombia-risk-map
```

O descargue el ZIP y extráigalo en su computador.

---

### Paso 2 — Obtener clave de API de Google Maps

1. Vaya a [Google Cloud Console](https://console.cloud.google.com/)
2. Cree un proyecto nuevo o seleccione uno existente
3. Vaya a **APIs y Servicios → Biblioteca**
4. Busque y habilite: **Maps JavaScript API**
5. Vaya a **APIs y Servicios → Credenciales**
6. Haga clic en **Crear credenciales → Clave de API**
7. Copie la clave generada

> ⚠️ **Recomendado:** Restrinja la clave a su dominio de GitHub Pages para evitar uso no autorizado.  
> En Restricciones de aplicación → Sitios web HTTP → agregue: `https://SU_USUARIO.github.io/*`

---

### Paso 3 — Publicar en GitHub Pages (gratis)

1. Cree un repositorio en [github.com](https://github.com/new) con el nombre `colombia-risk-map`
2. Suba todos los archivos a ese repositorio:
   ```bash
   git remote add origin https://github.com/SU_USUARIO/colombia-risk-map.git
   git branch -M main
   git push -u origin main
   ```
3. En su repositorio en GitHub, vaya a **Settings → Pages**
4. En "Source" seleccione: **Deploy from a branch → main → / (root)**
5. Haga clic en **Save**
6. En ~2 minutos, su mapa estará disponible en:
   ```
   https://SU_USUARIO.github.io/colombia-risk-map/
   ```

---

### Paso 4 — Configurar secretos para actualizaciones automáticas

Para que el mapa se actualice automáticamente cada domingo, debe configurar dos secretos en GitHub:

1. En su repositorio, vaya a **Settings → Secrets and variables → Actions**
2. Haga clic en **New repository secret** y agregue:

| Nombre del secreto     | Valor                                    |
|------------------------|------------------------------------------|
| `ANTHROPIC_API_KEY`    | Su clave de API de Anthropic             |
| `GOOGLE_MAPS_API_KEY`  | Su clave de API de Google Maps           |

> Obtenga su clave de Anthropic en: [console.anthropic.com](https://console.anthropic.com/)

---

### Paso 5 — Verificar la actualización automática

- Vaya a la pestaña **Actions** en su repositorio de GitHub
- Verá el flujo de trabajo: **"Actualizar Mapa de Riesgo (Dom 23:00 COT)"**
- Para probar manualmente: haga clic en el flujo → **Run workflow**

---

## 🔄 Cómo funciona la actualización automática

```
Cada domingo 23:00 COT (04:00 UTC lunes)
         ↓
GitHub Actions ejecuta actualizador_mapa.py
         ↓
Script llama a la API de Anthropic con búsqueda web
         ↓
Claude busca y analiza incidentes recientes en Colombia
         ↓
Genera datos JSON actualizados (municipios + vías)
         ↓
Script actualiza index.html con los nuevos datos
         ↓
GitHub Pages publica automáticamente la nueva versión
         ↓
El mapa en línea muestra la inteligencia más reciente
```

---

## 📁 Estructura del repositorio

```
colombia-risk-map/
├── index.html              ← Mapa interactivo (Google Maps + datos de riesgo)
├── actualizador_mapa.py    ← Script de actualización automática
├── .github/
│   └── workflows/
│       └── actualizar-mapa.yml  ← Programación y flujo de GitHub Actions
└── README.md               ← Este archivo
```

---

## 🔒 Seguridad y confidencialidad

- Este repositorio debe ser **PRIVADO** en GitHub (Settings → Danger Zone → Make private)
- Las claves de API están almacenadas como secretos cifrados de GitHub, nunca en el código
- El mapa contiene información operacional sensible — comparta el enlace solo con personal autorizado
- Considere agregar autenticación básica si el repositorio debe ser público

---

## 🎨 Clasificación de zonas (criterios CoH)

| Zona | Criterio | Postura operacional |
|------|----------|---------------------|
| 🔴 ROJO | Combate activo · amenaza directa · colapso de acceso | Operaciones SUSPENDIDAS |
| 🟠 NARANJA | 3+ incidentes/30 días · presencia armada <30 km | Solo actividades ESENCIALES |
| 🟡 AMARILLO | 1–2 incidentes/30 días · actividad esporádica <50 km | Monitoreo REFORZADO |
| 🟢 VERDE | Sin indicadores de conflicto activo | Operaciones ESTÁNDAR |

---

## ⚙️ Actualización manual

Para actualizar el mapa manualmente desde su computador:

```bash
# Instalar dependencias
pip install anthropic python-dateutil

# Configurar variables de entorno
export ANTHROPIC_API_KEY="su-clave-anthropic"
export GOOGLE_MAPS_API_KEY="su-clave-google-maps"

# Ejecutar actualizador
python actualizador_mapa.py

# Subir cambios
git add index.html
git commit -m "Actualización manual del mapa"
git push
```

---

## 📞 Soporte

Para problemas técnicos con la configuración, contacte al equipo de operaciones de campo de CoH Colombia.

**Fuentes de inteligencia:** Indepaz · ICG · ACLED · WOLA · OCHA · Crisis Group · Defensoría del Pueblo Colombia

---

*Clasificación: CONFIDENCIAL · Solo Personal Autorizado · Plan de Contingencia CoH v2.0*
