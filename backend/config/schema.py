import graphene

from apps.exams.graphql import Query as ExamsQuery


class Query(ExamsQuery, graphene.ObjectType):
    pass


schema = graphene.Schema(query=Query)
