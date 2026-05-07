name: Actualizar Mapa de Riesgo (Dom 23:00 COT)

on:
  schedule:
    - cron: '0 4 * * 1'
  workflow_dispatch:
    inputs:
      motivo:
        description: 'Motivo de la actualizacion manual'
        required: false
        default: 'Actualizacion manual solicitada'

jobs:
  actualizar-mapa:
    name: Buscar inteligencia y actualizar mapa
    runs-on: ubuntu-latest
    permissions:
      contents: write

    steps:
      - name: Clonar repositorio
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Configurar Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: Instalar dependencias
        run: |
          python -m pip install --upgrade pip
          pip install anthropic

      - name: Ejecutar actualizador del mapa
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          python actualizador_mapa.py

      - name: Verificar si hay cambios
        id: verificar
        run: |
          git diff --quiet index.html && echo "sin_cambios=true" >> $GITHUB_OUTPUT || echo "sin_cambios=false" >> $GITHUB_OUTPUT

      - name: Commit y push de cambios
        if: steps.verificar.outputs.sin_cambios == 'false'
        run: |
          git config user.name "CoH Risk Map Bot"
          git config user.email "actions@github.com"
          FECHA_COT=$(TZ='America/Bogota' date '+%d %b %Y %H:%M COT')
          git add index.html
          git commit -m "Actualizacion automatica del mapa - ${FECHA_COT}"
          git push

      - name: Sin cambios esta semana
        if: steps.verificar.outputs.sin_cambios == 'true'
        run: echo "El mapa no requirio cambios esta semana."
