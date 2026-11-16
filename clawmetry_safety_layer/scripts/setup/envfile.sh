# write_env_once: create .env from .env.example with fresh random keys when no .env exists (sourced by setup_*.sh)
write_env_once() {
  if [ -f .env ]; then ok ".env exists (left as is)"; return; fi
  local ek bk
  ek="$("$PY" -c 'import secrets;print(secrets.token_urlsafe(24))')"
  bk="$("$PY" -c 'import secrets;print(secrets.token_urlsafe(24))')"
  ck="$("$PY" -c 'import secrets;print(secrets.token_urlsafe(24))')"
  sed -e "s|^EDGE_API_KEY=.*|EDGE_API_KEY=$ek|" -e "s|^BRAIN_API_KEY=.*|BRAIN_API_KEY=$bk|" \
      -e "s|^COSMOS_API_KEY=.*|COSMOS_API_KEY=$ck|" .env.example > .env
  chmod 600 .env
  ok ".env written with fresh EDGE_API_KEY / BRAIN_API_KEY (copy the SAME two keys to the other machine's .env)"
}
