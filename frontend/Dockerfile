FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm install --no-audit --no-fund
COPY . .
ENV BACKEND_URL=http://backend:8000
RUN npm run build
FROM node:22-bookworm-slim
WORKDIR /app
COPY --from=build /app /app
ENV NODE_ENV=production
ENV BACKEND_URL=http://backend:8000
USER node
EXPOSE 3000
CMD ["npm","start","--","--hostname","0.0.0.0"]
