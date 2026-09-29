FROM node:22-alpine AS build
WORKDIR /app
COPY package.json ./
# @emergentbase/* are optional editor overlays served from a private registry.
RUN node -e "const p=require('./package.json');for(const k of ['dependencies','devDependencies'])for(const n of Object.keys(p[k]||{}))if(n.startsWith('@emergentbase/'))delete p[k][n];require('fs').writeFileSync('package.json',JSON.stringify(p,null,2))" \
 && yarn install --ignore-engines --network-timeout 600000
COPY . .
# Empty backend URL = same origin; nginx proxies /api to the backend.
ENV REACT_APP_BACKEND_URL="" GENERATE_SOURCEMAP=false DISABLE_ESLINT_PLUGIN=true
RUN npx craco build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/build /usr/share/nginx/html
EXPOSE 80
