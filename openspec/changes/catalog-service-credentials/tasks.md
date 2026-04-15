## 1. Config

- [ ] 1.1 Add `service_token: str` field (optional, default empty string) to `Settings` in `config.py`, reading from `SERVICE_TOKEN` env var
- [ ] 1.2 Add `SERVICE_TOKEN=` entry (with explanatory comment) to `.env.example` if it exists, and to the repo `.env` template

## 2. Validation Pipeline

- [ ] 2.1 Remove `token: str = ""` parameter from `check_mat_table`, `check_name_reservation`, and `run_validation_pipeline`
- [ ] 2.2 In `check_mat_table` and `check_name_reservation`, read the service token from `get_settings().service_token` and use it in the `Authorization` header (keep existing skip logic when token is empty)
- [ ] 2.3 Update callers in `routers/assets.py` (`register_asset` and `validate_asset`) to remove the `token=user.token` argument from `run_validation_pipeline` calls

## 3. Logging

- [ ] 3.1 Add `service_token_configured` (bool) to the `config` debug log in `app.py` startup so operators can verify the token is being picked up without logging the token value itself

## 4. Tests

- [ ] 4.1 Update existing validation tests that pass `token=` to `run_validation_pipeline` / `check_mat_table` / `check_name_reservation` to remove that argument
- [ ] 4.2 Add a test verifying that when `SERVICE_TOKEN` is set, ME API calls include `Authorization: Bearer <token>`
- [ ] 4.3 Add a test verifying that when `SERVICE_TOKEN` is absent, ME calls proceed without an Authorization header (and skip gracefully on auth error)
