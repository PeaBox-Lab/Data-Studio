# Python Data Workbench v0.18 - Deployment APIs and REST

## New in v0.18

### Deployment contracts
- Create named REST deployments from registered model versions
- Versioned API and model metadata
- Feature schema, target, problem type and endpoint definitions
- Individual and batch prediction routes

### FastAPI export
Each deployment can export a standalone FastAPI package containing:
- `server.py`
- Fitted `model.pkl`
- `deployment.json`
- `sample_request.json`
- API-specific `requirements.txt`
- Windows and Linux launchers
- API README

Generated endpoints:
- `GET /health`
- `GET /metadata`
- `POST /v1/models/<name>/predict`
- `POST /v1/models/<name>/predict-batch`
- Interactive OpenAPI documentation at `/docs`

### Validation and scoring
- Reject undeclared request fields
- Validate required model features
- Return class probabilities and class labels for probability-capable classifiers
- Test the deployment contract locally without starting a server
- Batch-score the active dataset and return predictions to the grid
- Add maximum class confidence for classifiers

### Operational history
- Record test and batch requests
- Record row counts, status, errors and output columns
- Deployment and API history persist in project files

## Run
Extract and run `run_workbench_v0.18.bat`.

## Security
Generated APIs load a Python pickle model. Deploy only trusted packages. Production deployment should use authentication, TLS, request-size limits, structured logging, restricted service accounts, dependency locking and a reverse proxy such as NGINX, Azure API Management or AWS API Gateway.
