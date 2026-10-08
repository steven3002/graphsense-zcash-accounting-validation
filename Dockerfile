# Build installs the dependencies (uses the network); run validates (needs none):
#     docker build -t gs-zec-validate .
#     docker run --rm --network none gs-zec-validate
# To keep the report, add:  -v "$PWD/report:/report"
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

ENV PYTHONDONTWRITEBYTECODE=1 \
    GS_ZEC_VALIDATE_ENV_DIR=/opt/gs-zec-validate/envs
WORKDIR /validation
COPY . .
RUN python gs-zec-validate.py install --installer pip

ENTRYPOINT ["python", "gs-zec-validate.py"]
CMD ["run", "--report-dir", "/report"]
