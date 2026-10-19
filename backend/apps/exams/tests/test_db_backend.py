"""Nova tentativa de conexão (`config.db_backend`), sem banco e sem rede.

O compute do Neon (plano gratuito) é suspenso depois de 5 minutos ociosos; a primeira conexão
depois disso pode falhar de leve enquanto ele acorda. A nova tentativa só existe no caminho que
ABRE a conexão: com uma conexão aberta (dentro de uma transação, por exemplo) ela nunca roda.
"""

import copy
import logging
from unittest import mock

import psycopg
import pytest
from django.db import connection
from django.db.backends.postgresql.base import DatabaseWrapper as StockWrapper

from config.db_backend import base as backend

SECRET = "senha-inventada-123"


def make_wrapper(retries: int | None) -> backend.DatabaseWrapper:
    settings_dict = copy.deepcopy(connection.settings_dict)
    if retries is not None:
        settings_dict["CONNECT_RETRIES"] = retries
    return backend.DatabaseWrapper(settings_dict, alias="neon_test")


@pytest.fixture
def stock():
    with mock.patch.object(StockWrapper, "get_new_connection") as patched:
        yield patched


@pytest.fixture
def sleep():
    with mock.patch.object(backend.time, "sleep") as patched:
        yield patched


def test_a_working_connection_is_returned_without_waiting(stock, sleep):
    stock.return_value = "conexao"

    assert make_wrapper(1).get_new_connection({}) == "conexao"
    assert stock.call_count == 1
    sleep.assert_not_called()


def test_one_operational_error_is_retried_once_after_a_short_pause(stock, sleep):
    stock.side_effect = [psycopg.OperationalError("compute acordando"), "conexao"]

    assert make_wrapper(1).get_new_connection({"host": "h"}) == "conexao"
    assert stock.call_count == 2
    sleep.assert_called_once_with(backend.RETRY_DELAY_SECONDS)


def test_it_gives_up_after_the_configured_retries_and_raises_the_last_error(stock, sleep):
    stock.side_effect = psycopg.OperationalError("ainda fora do ar")

    with pytest.raises(psycopg.OperationalError, match="ainda fora do ar"):
        make_wrapper(2).get_new_connection({})

    assert stock.call_count == 3 and sleep.call_count == 2


@pytest.mark.parametrize("retries", [None, 0])
def test_without_retries_the_first_error_is_raised_immediately(stock, sleep, retries):
    stock.side_effect = psycopg.OperationalError("falhou")

    with pytest.raises(psycopg.OperationalError):
        make_wrapper(retries).get_new_connection({})

    assert stock.call_count == 1
    sleep.assert_not_called()


def test_other_errors_are_not_retried(stock, sleep):
    stock.side_effect = psycopg.ProgrammingError("parâmetro inválido")

    with pytest.raises(psycopg.ProgrammingError):
        make_wrapper(3).get_new_connection({})

    assert stock.call_count == 1
    sleep.assert_not_called()


def test_the_log_never_carries_the_error_text_or_connection_parameters(stock, sleep, caplog):
    stock.side_effect = [psycopg.OperationalError(f"user=u password={SECRET}"), "conexao"]

    with caplog.at_level(logging.WARNING, logger=backend.logger.name):
        make_wrapper(1).get_new_connection({"password": SECRET})

    assert caplog.records and SECRET not in caplog.text and "user=u" not in caplog.text


def test_an_open_connection_is_never_reconnected_so_a_transaction_is_never_retried(
    stock, django_db_blocker
):
    wrapper = make_wrapper(5)
    wrapper.connection = mock.MagicMock()  # conexão já aberta (por exemplo, dentro de atomic)
    wrapper.connection.closed = False

    with django_db_blocker.unblock():  # não acessa o banco: só prova que não reconecta
        wrapper.ensure_connection()

    stock.assert_not_called()
