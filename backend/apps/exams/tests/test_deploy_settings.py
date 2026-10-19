"""Configuração de deploy lida do ambiente (`config/envconf.py`): sem banco, sem rede.

Senhas e URLs aqui são inventadas.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.envconf import (
    detect_pooler,
    env_bool,
    env_int,
    env_list,
    parse_database_url,
    resolve_database,
    resolve_infra,
    validate_schema,
)

# Valores inventados com o formato da connection string do Neon (host `-pooler` = PgBouncer).
NEON_USER = "myexams_app"
NEON_PASSWORD = "senha-inventada-123"
NEON_DIRECT_HOST = "ep-example-123456.sa-east-1.aws.neon.tech"
NEON_POOLED_HOST = "ep-example-123456-pooler.sa-east-1.aws.neon.tech"
NEON_QUERY = "sslmode=require&channel_binding=require"
NEON_DIRECT_URL = f"postgresql://{NEON_USER}:{NEON_PASSWORD}@{NEON_DIRECT_HOST}/neondb?{NEON_QUERY}"
NEON_POOLED_URL = f"postgresql://{NEON_USER}:{NEON_PASSWORD}@{NEON_POOLED_HOST}/neondb?{NEON_QUERY}"

# --- parse_database_url ---------------------------------------------------------------------


def test_parses_every_part_of_a_full_url():
    parsed = parse_database_url("postgresql://usuario:segredo@db.exemplo.test:6543/meubanco")

    assert (parsed.user, parsed.password) == ("usuario", "segredo")
    assert (parsed.host, parsed.port, parsed.name) == ("db.exemplo.test", "6543", "meubanco")
    assert parsed.params == {}


def test_decodes_special_characters_in_user_and_password():
    parsed = parse_database_url("postgres://usu%2Fario:p%40ss%3Aw%2Frd%23%3F%25@h.test/db")

    assert parsed.user == "usu/ario"
    assert parsed.password == "p@ss:w/rd#?%"
    assert parsed.host == "h.test"


def test_tolerates_a_raw_at_sign_inside_the_password():
    # O host nunca tem `@`, então o último `@` separa as credenciais.
    parsed = parse_database_url("postgresql://u:a@b@h.test:5432/db")

    assert (parsed.user, parsed.password, parsed.host) == ("u", "a@b", "h.test")


def test_keeps_only_known_libpq_options_from_the_query_string():
    parsed = parse_database_url(
        "postgresql://u:p@h.test/db?sslmode=require&connect_timeout=5&unknown=1&x="
    )

    assert parsed.params == {"sslmode": "require", "connect_timeout": "5"}


def test_neon_shaped_url_keeps_every_libpq_parameter_neon_documents():
    url = (
        f"postgresql://{NEON_USER}:{NEON_PASSWORD}@{NEON_POOLED_HOST}/neondb"
        "?sslmode=verify-full&channel_binding=require&sslrootcert=system"
        "&sslnegotiation=direct&connect_timeout=15&application_name=myexams"
        "&options=-c%20statement_timeout%3D5000"
    )
    parsed = parse_database_url(url)

    assert (parsed.user, parsed.password) == (NEON_USER, NEON_PASSWORD)
    assert (parsed.host, parsed.port, parsed.name) == (NEON_POOLED_HOST, "5432", "neondb")
    assert parsed.params == {
        "sslmode": "verify-full",
        "channel_binding": "require",
        "sslrootcert": "system",
        "sslnegotiation": "direct",
        "connect_timeout": "15",
        "application_name": "myexams",
        "options": "-c statement_timeout=5000",
    }


def test_missing_port_defaults_to_5432_and_password_and_user_may_be_absent():
    parsed = parse_database_url("postgresql://h.test/db")

    assert (parsed.user, parsed.password, parsed.port) == ("", "", "5432")


def test_user_without_password_and_unix_socket_style_url():
    assert parse_database_url("postgresql://soze@h.test/db").password == ""
    parsed = parse_database_url("postgresql:///db")
    assert (parsed.host, parsed.name) == ("", "db")


def test_ipv6_host_and_port():
    parsed = parse_database_url("postgresql://u:p@[2001:db8::1]:6000/db")

    assert (parsed.host, parsed.port) == ("2001:db8::1", "6000")


@pytest.mark.parametrize(
    "url",
    [
        "mysql://u:p@h.test/db",
        "h.test/db",
        "postgresql://u:p@h.test",
        "postgresql://u:p@h.test/",
        "postgresql://u:p@h.test:abc/db",
        "postgresql://u:p@h.test:99999/db",
    ],
)
def test_invalid_urls_are_refused_without_echoing_the_url(url):
    with pytest.raises(ImproperlyConfigured) as info:
        parse_database_url(url)

    assert "p@h" not in str(info.value) and ":p@" not in str(info.value)


# --- resolve_database -----------------------------------------------------------------------


def test_without_database_url_the_postgres_variables_win_as_today():
    config = resolve_database({}).default

    assert config["NAME"] == "myexams" and config["HOST"] == "localhost"
    assert config["PORT"] == "5433" and config["PASSWORD"] == "myexams"
    assert config["OPTIONS"] == {"sslmode": "prefer"}
    assert config["CONN_MAX_AGE"] == 0
    assert "DISABLE_SERVER_SIDE_CURSORS" not in config


def test_postgres_variables_are_used_when_present():
    env = {
        "POSTGRES_DB": "d",
        "POSTGRES_USER": "u",
        "POSTGRES_PASSWORD": "p",
        "POSTGRES_HOST": "h",
        "POSTGRES_PORT": "1",
        "POSTGRES_SSLMODE": "disable",
    }
    config = resolve_database(env).default

    assert (config["NAME"], config["USER"], config["PASSWORD"]) == ("d", "u", "p")
    assert (config["HOST"], config["PORT"], config["OPTIONS"]) == ("h", "1", {"sslmode": "disable"})


def test_database_url_wins_over_the_postgres_variables():
    env = {"DATABASE_URL": "postgresql://u:p%40x@db.exemplo.test:5432/prod", "POSTGRES_DB": "no"}
    config = resolve_database(env).default

    assert (config["NAME"], config["USER"], config["PASSWORD"]) == ("prod", "u", "p@x")
    assert config["HOST"] == "db.exemplo.test"


def test_remote_url_without_sslmode_requires_ssl_but_local_url_keeps_prefer():
    remote = resolve_database({"DATABASE_URL": "postgresql://u:p@db.exemplo.test/d"}).default
    local = resolve_database({"DATABASE_URL": "postgresql://u:p@localhost:5433/d"}).default

    assert remote["OPTIONS"]["sslmode"] == "require"
    assert local["OPTIONS"]["sslmode"] == "prefer"


def test_sslmode_from_the_url_beats_the_environment():
    env = {"DATABASE_URL": "postgresql://u:p@h.test/d?sslmode=verify-full", "POSTGRES_SSLMODE": "x"}

    assert resolve_database(env).default["OPTIONS"]["sslmode"] == "verify-full"


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        (NEON_POOLED_HOST, "transaction"),
        ("EP-EXAMPLE-123456-POOLER.sa-east-1.aws.neon.tech", "transaction"),
        (NEON_DIRECT_HOST, ""),
        ("localhost", ""),
        ("db.exemplo.test", ""),
        ("pooler.exemplo.test", ""),
        ("", ""),
    ],
)
def test_pooler_detection_looks_for_the_neon_pooler_suffix_in_the_endpoint(host, expected):
    assert detect_pooler(host) == expected


def test_neon_pooled_url_is_a_transaction_pooler_without_any_port_hint():
    result = resolve_database({"DATABASE_URL": NEON_POOLED_URL, "DATABASE_CONN_MAX_AGE": "600"})
    config = result.default

    assert result.pooler == "transaction"
    assert config["DISABLE_SERVER_SIDE_CURSORS"] is True
    assert config["CONN_MAX_AGE"] == 0
    assert config["OPTIONS"]["prepare_threshold"] is None
    assert config["PORT"] == "5432"
    # a connection string do Neon vem com estes dois e o libpq precisa recebê-los
    assert config["OPTIONS"]["sslmode"] == "require"
    assert config["OPTIONS"]["channel_binding"] == "require"


def test_neon_direct_url_keeps_server_side_cursors_and_prepared_statements():
    result = resolve_database({"DATABASE_URL": NEON_DIRECT_URL})

    assert result.pooler == ""
    assert "DISABLE_SERVER_SIDE_CURSORS" not in result.default
    assert "prepare_threshold" not in result.default["OPTIONS"]


def test_libpq_accepts_the_connection_parameters_built_from_a_neon_url():
    from psycopg.conninfo import make_conninfo

    options = resolve_database({"DATABASE_URL": NEON_POOLED_URL}).default["OPTIONS"]
    libpq_options = {k: v for k, v in options.items() if k != "prepare_threshold"}

    info = make_conninfo("", host=NEON_POOLED_HOST, dbname="neondb", **libpq_options)

    assert "channel_binding=require" in info and "sslmode=require" in info


def test_explicit_pooler_setting_overrides_detection():
    env = {
        "DATABASE_URL": "postgresql://u:p@db.exemplo.test:6432/d",
        "DATABASE_POOLER": "transaction",
    }
    assert resolve_database(env).default["DISABLE_SERVER_SIDE_CURSORS"] is True

    env = {"DATABASE_URL": NEON_POOLED_URL, "DATABASE_POOLER": "direct"}
    config = resolve_database(env).default
    assert "DISABLE_SERVER_SIDE_CURSORS" not in config
    assert "prepare_threshold" not in config["OPTIONS"]


def test_direct_keeps_the_current_behaviour():
    env = {"DATABASE_URL": "postgresql://u:p@db.exemplo.test/d", "DATABASE_POOLER": "direct"}
    config = resolve_database(env).default

    assert "DISABLE_SERVER_SIDE_CURSORS" not in config
    assert "prepare_threshold" not in config["OPTIONS"]


@pytest.mark.parametrize("value", ["pgbouncer", "session"])
def test_unknown_pooler_is_refused(value):
    with pytest.raises(ImproperlyConfigured, match="DATABASE_POOLER"):
        resolve_database({"DATABASE_POOLER": value})


def test_conn_max_age_can_be_set_outside_transaction_mode_and_turns_on_health_checks():
    env = {"DATABASE_URL": NEON_DIRECT_URL, "DATABASE_CONN_MAX_AGE": "60"}
    config = resolve_database(env).default

    # o Neon suspende o compute ocioso: uma conexão reaproveitada pode ter sido encerrada
    assert config["CONN_MAX_AGE"] == 60 and config["CONN_HEALTH_CHECKS"] is True


def test_default_connection_reuse_is_off_and_adds_no_health_checks():
    for env in ({}, {"DATABASE_URL": NEON_DIRECT_URL}):
        assert "CONN_HEALTH_CHECKS" not in resolve_database(env).default


# --- Cold start do Neon: connect_timeout e uma nova tentativa ------------------------------


def test_local_database_gets_no_timeout_and_no_retry():
    for env in ({}, {"DATABASE_URL": "postgresql://u:p@localhost:5433/d"}):
        config = resolve_database(env).default

        assert "connect_timeout" not in config["OPTIONS"]
        assert "CONNECT_RETRIES" not in config
        assert config["ENGINE"] == "django.db.backends.postgresql"


def test_remote_database_gets_a_ten_second_timeout_and_one_retry():
    config = resolve_database({"DATABASE_URL": NEON_POOLED_URL}).default

    assert config["OPTIONS"]["connect_timeout"] == "10"
    assert config["CONNECT_RETRIES"] == 1
    assert config["ENGINE"] == "config.db_backend"


def test_connect_timeout_and_retries_can_be_tuned_or_turned_off():
    env = {
        "DATABASE_URL": NEON_POOLED_URL,
        "DATABASE_CONNECT_TIMEOUT": "20",
        "DATABASE_CONNECT_RETRIES": "2",
    }
    config = resolve_database(env).default
    assert config["OPTIONS"]["connect_timeout"] == "20" and config["CONNECT_RETRIES"] == 2

    off = resolve_database(
        {
            "DATABASE_URL": NEON_POOLED_URL,
            "DATABASE_CONNECT_TIMEOUT": "0",
            "DATABASE_CONNECT_RETRIES": "0",
        }
    ).default
    assert "connect_timeout" not in off["OPTIONS"] and "CONNECT_RETRIES" not in off
    assert off["ENGINE"] == "django.db.backends.postgresql"


def test_connect_timeout_can_be_forced_on_a_local_database():
    env = {"DATABASE_URL": "postgresql://u:p@localhost:5433/d", "DATABASE_CONNECT_TIMEOUT": "5"}

    assert resolve_database(env).default["OPTIONS"]["connect_timeout"] == "5"


def test_connect_timeout_from_the_url_wins_over_the_default():
    url = f"postgresql://u:p@{NEON_DIRECT_HOST}/d?connect_timeout=15"

    assert resolve_database({"DATABASE_URL": url}).default["OPTIONS"]["connect_timeout"] == "15"


@pytest.mark.parametrize("name", ["DATABASE_CONNECT_TIMEOUT", "DATABASE_CONNECT_RETRIES"])
@pytest.mark.parametrize("value", ["-1", "x"])
def test_invalid_timeout_and_retries_are_refused(name, value):
    with pytest.raises(ImproperlyConfigured, match=name):
        resolve_database({"DATABASE_URL": NEON_POOLED_URL, name: value})


# --- Esquema --------------------------------------------------------------------------------


def test_default_schema_is_empty_and_adds_no_search_path():
    result = resolve_database({"DATABASE_URL": "postgresql://u:p@h.test/d"})

    assert result.schema == ""
    assert "options" not in result.default["OPTIONS"]


@pytest.mark.parametrize("value", ["", "public", " public "])
def test_public_means_no_dedicated_schema(value):
    assert validate_schema(value) == ""


def test_dedicated_schema_goes_into_the_search_path_followed_by_public():
    result = resolve_database({"DATABASE_URL": NEON_DIRECT_URL, "DATABASE_SCHEMA": "myexams"})

    assert result.schema == "myexams"
    assert result.default["OPTIONS"]["options"] == "-c search_path=myexams,public"


def test_search_path_is_appended_to_options_from_the_url():
    env = {"DATABASE_URL": "postgresql://u:p@h.test/d?options=-c%20statement_timeout%3D5000"}
    env["DATABASE_SCHEMA"] = "myexams"

    options = resolve_database(env).default["OPTIONS"]["options"]

    assert options == "-c statement_timeout=5000 -c search_path=myexams,public"


def test_transaction_pooler_does_not_send_startup_search_path():
    # O PgBouncer do Neon recusa `options=-c search_path=...` ("unsupported startup parameter"):
    # o search_path vem do papel (ALTER ROLE ... SET search_path), ver docs/DEPLOY.md.
    env = {"DATABASE_URL": NEON_POOLED_URL, "DATABASE_SCHEMA": "myexams"}
    result = resolve_database(env)

    assert result.schema == "myexams"
    assert "options" not in result.default["OPTIONS"]


@pytest.mark.parametrize("value", ["My-Schema", "1abc", "a b", 'x";drop', "a" * 64, "ex.tra"])
def test_unsafe_schema_names_are_refused(value):
    with pytest.raises(ImproperlyConfigured, match="DATABASE_SCHEMA"):
        validate_schema(value)


# --- Migração da nuvem ----------------------------------------------------------------------


def test_cloud_migrate_mode_uses_the_direct_migrate_url_not_the_runtime_url():
    env = {
        "EXAMS_CLOUD_MIGRATE": "1",
        "DATABASE_URL": NEON_POOLED_URL,
        "DATABASE_MIGRATE_URL": NEON_DIRECT_URL,
    }
    result = resolve_database(env)

    assert result.migrate_mode is True
    assert result.default["HOST"] == NEON_DIRECT_HOST
    assert result.pooler == ""
    assert "DISABLE_SERVER_SIDE_CURSORS" not in result.default
    assert "prepare_threshold" not in result.default["OPTIONS"]


def test_cloud_migrate_mode_requires_the_migrate_url():
    env = {"EXAMS_CLOUD_MIGRATE": "1", "DATABASE_URL": "postgresql://u:p@h.test/d"}

    with pytest.raises(ImproperlyConfigured, match="DATABASE_MIGRATE_URL"):
        resolve_database(env)


def test_cloud_migrate_mode_refuses_the_pooled_endpoint():
    env = {"EXAMS_CLOUD_MIGRATE": "1", "DATABASE_MIGRATE_URL": NEON_POOLED_URL}

    with pytest.raises(ImproperlyConfigured, match="modo transação") as info:
        resolve_database(env)

    assert NEON_PASSWORD not in str(info.value)


def test_cloud_migrate_pooler_override_is_separate_from_the_runtime_one():
    env = {
        "EXAMS_CLOUD_MIGRATE": "1",
        "DATABASE_POOLER": "transaction",
        "DATABASE_MIGRATE_URL": NEON_DIRECT_URL,
    }

    assert resolve_database(env).pooler == ""


def test_migrate_url_is_ignored_outside_cloud_migrate_mode():
    env = {
        "DATABASE_URL": "postgresql://u:p@a.test/runtime",
        "DATABASE_MIGRATE_URL": "postgresql://u:p@b.test/m",
    }

    assert resolve_database(env).default["NAME"] == "runtime"


# --- Celery, canais e cache -----------------------------------------------------------------


def test_default_infrastructure_is_redis_everywhere_as_today():
    infra = resolve_infra({})

    assert infra.eager is False
    assert infra.broker_url == infra.result_backend == "redis://localhost:6380/0"
    assert infra.channel_layers["default"]["BACKEND"].endswith("RedisChannelLayer")
    assert infra.caches["default"]["BACKEND"].endswith("RedisCache")


def test_redis_url_is_used_when_set():
    infra = resolve_infra({"REDIS_URL": "redis://r.test:1/2"})

    assert infra.broker_url == "redis://r.test:1/2"
    assert infra.caches["default"]["LOCATION"] == "redis://r.test:1/2"


def test_inline_mode_never_points_celery_at_redis_and_uses_in_memory_channels():
    infra = resolve_infra({"CELERY_TASK_ALWAYS_EAGER": "true"})

    assert infra.eager is True
    assert infra.broker_url == "memory://localhost/"
    assert infra.result_backend is None
    assert infra.channel_layers["default"]["BACKEND"] == "channels.layers.InMemoryChannelLayer"


def test_inline_mode_with_an_explicit_redis_url_keeps_redis_channels():
    infra = resolve_infra({"CELERY_TASK_ALWAYS_EAGER": "1", "REDIS_URL": "redis://r.test/0"})

    assert infra.broker_url == "memory://localhost/"
    assert infra.channel_layers["default"]["BACKEND"].endswith("RedisChannelLayer")


def test_channel_layer_can_be_forced_off_or_in_memory():
    assert resolve_infra({"DJANGO_CHANNEL_LAYER": "none"}).channel_layers == {}
    memory = resolve_infra({"DJANGO_CHANNEL_LAYER": "memory"}).channel_layers
    assert memory["default"]["BACKEND"].endswith("InMemoryChannelLayer")


def test_database_cache_lives_in_the_django_cache_table():
    cache = resolve_infra({"DJANGO_CACHE": "db"}).caches["default"]

    assert cache["BACKEND"] == "django.core.cache.backends.db.DatabaseCache"
    assert cache["LOCATION"] == "django_cache"


def test_locmem_cache_option():
    cache = resolve_infra({"DJANGO_CACHE": "locmem"}).caches["default"]

    assert cache["BACKEND"].endswith("LocMemCache")


@pytest.mark.parametrize("name", ["DJANGO_CACHE", "DJANGO_CHANNEL_LAYER"])
def test_unknown_modes_are_refused(name):
    with pytest.raises(ImproperlyConfigured, match=name):
        resolve_infra({name: "memcached"})


# --- Auxiliares -----------------------------------------------------------------------------


def test_env_bool_accepts_the_usual_spellings_and_rejects_nonsense():
    assert env_bool({"A": "True"}, "A") is True
    assert env_bool({"A": " off "}, "A", True) is False
    assert env_bool({}, "A", True) is True
    with pytest.raises(ImproperlyConfigured, match="A"):
        env_bool({"A": "talvez"}, "A")


def test_env_int_and_env_list():
    assert env_int({"N": " 7 "}, "N", 1) == 7
    assert env_int({"N": ""}, "N", 1) == 1
    with pytest.raises(ImproperlyConfigured, match="N"):
        env_int({"N": "x"}, "N", 1)
    assert env_list({"L": " a , ,b "}, "L", ["z"]) == ["a", "b"]
    assert env_list({}, "L", ["z"]) == ["z"]
