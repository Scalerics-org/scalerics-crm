# El build se hace en dos etapas por better-sqlite3, que es nativo: necesita
# compilador y python para instalarse, pero nada de eso hace falta despues.
FROM node:22-slim AS build
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM node:22-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /app/node_modules ./node_modules
COPY package.json ./
COPY src ./src
# Las herramientas de mano, que se corren con `fly ssh console`: sin esto el
# informe de Jev (npm run jev:informe) no existe en la maquina y habria que
# bajarse la base para mirarla. Son unos KB y ninguna corre sola.
COPY scripts ./scripts

ENV NODE_ENV=production
# El volumen se monta en /data: ahi viven la sesion y la base, que son lo unico
# que no se puede perder ni reconstruir.
ENV BAILEYS_AUTH_DIR=/data/auth
ENV DB_PATH=/data/wa.db

# Sin tini: en Fly el PID 1 es el init de la plataforma, asi que tini quedaba
# como hijo y no reparentaba nada — lo decia el propio log al arrancar. Las
# señales las entrega el init de Fly directo al proceso.
CMD ["node", "src/index.js"]
