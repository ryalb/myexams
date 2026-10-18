from rest_framework.pagination import PageNumberPagination


class UnboundedCollectionPagination(PageNumberPagination):
    """Paginação para coleções que crescem sem limite: laudos e resultados.

    O catálogo de exames é pequeno e fica sem paginação de propósito.
    `max_page_size` não é detalhe: sem teto, `?page_size=999999` derrota a
    paginação e o cliente volta a puxar a tabela inteira.
    """

    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 500
