# Web image: Next.js standalone build (only the files needed to run, no dev dependencies).
FROM node:22-alpine AS deps
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22-alpine AS build
WORKDIR /web
COPY --from=deps /web/node_modules ./node_modules
COPY . .
# Empty = same origin: the reverse proxy routes /api/* to the API, so the browser never calls another host
ARG NEXT_PUBLIC_API_URL=""
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine
WORKDIR /web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN adduser -D -u 10001 webuser
COPY --from=build --chown=webuser /web/.next/standalone ./
COPY --from=build --chown=webuser /web/.next/static ./.next/static
COPY --from=build --chown=webuser /web/public ./public
USER webuser
EXPOSE 3000
CMD ["node", "server.js"]
