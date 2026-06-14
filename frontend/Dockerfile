ARG BASE_IMAGE=ghcr.io/nginxinc/nginx-unprivileged:alpine
FROM ${BASE_IMAGE}

COPY vue-project/dist/ /usr/share/nginx/html/
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
