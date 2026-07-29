# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2021-present Kaleidos INC

from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.db.models import Q, OuterRef, Subquery
from django.utils.translation import gettext as _

from taiga.base import filters
from taiga.base.utils.db import to_tsquery


def get_assigned_users_filter(model, value):
    assigned_users_ids = model.objects.order_by().filter(
        assigned_users__in=value, id=OuterRef('pk')).values('pk')

    assigned_user_filter = Q(pk__in=Subquery(assigned_users_ids))
    assigned_to_filter = Q(assigned_to__in=value)

    return Q(assigned_user_filter | assigned_to_filter)


class UserStoryQFilter(filters.FilterBackend):
    def _get_task_permission_clause(self, request, userstory_table):
        if request.user.is_authenticated and request.user.is_superuser:
            return "TRUE", []

        project_model = apps.get_model("projects", "Project")
        project_table = project_model._meta.db_table

        if request.user.is_authenticated:
            membership_model = apps.get_model("projects", "Membership")
            membership_table = membership_model._meta.db_table
            role_model = apps.get_model("users", "Role")
            role_table = role_model._meta.db_table

            permission_clause = """
                (
                    EXISTS (
                        SELECT 1
                        FROM {membership_table} AS search_task_membership
                        INNER JOIN {role_table} AS search_task_role
                            ON search_task_role.id = search_task_membership.role_id
                        WHERE search_task_membership.user_id = %s
                          AND search_task_membership.project_id = {userstory_table}.project_id
                          AND (
                              search_task_membership.is_admin = TRUE
                              OR %s = ANY(search_task_role.permissions)
                          )
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM {project_table} AS search_task_project
                        WHERE search_task_project.id = {userstory_table}.project_id
                          AND %s = ANY(search_task_project.public_permissions)
                    )
                )
            """.format(
                membership_table=membership_table,
                role_table=role_table,
                project_table=project_table,
                userstory_table=userstory_table,
            )
            return permission_clause, [request.user.pk, "view_tasks", "view_tasks"]

        permission_clause = """
            EXISTS (
                SELECT 1
                FROM {project_table} AS search_task_project
                WHERE search_task_project.id = {userstory_table}.project_id
                  AND %s = ANY(search_task_project.anon_permissions)
            )
        """.format(
            project_table=project_table,
            userstory_table=userstory_table,
        )
        return permission_clause, ["view_tasks"]

    def filter_queryset(self, request, queryset, view):
        q = request.QUERY_PARAMS.get("q", None)
        if not q:
            return queryset

        userstory_table = queryset.model._meta.db_table
        task_model = apps.get_model("tasks", "Task")
        task_table = task_model._meta.db_table
        attachment_model = apps.get_model("attachments", "Attachment")
        attachment_table = attachment_model._meta.db_table
        content_type_model = apps.get_model("contenttypes", "ContentType")
        userstory_content_type = content_type_model.objects.get_for_model(queryset.model)
        task_content_type = content_type_model.objects.get_for_model(task_model)
        tsquery = to_tsquery(q)
        task_permission_clause, task_permission_params = (
            self._get_task_permission_clause(request, userstory_table)
        )

        where_clause = """
            (
                to_tsvector(
                    'simple',
                    coalesce({userstory_table}.subject, '') || ' ' ||
                    coalesce(array_to_string({userstory_table}.tags, ' '), '') || ' ' ||
                    coalesce({userstory_table}.ref) || ' ' ||
                    coalesce({userstory_table}.description, '')
                ) @@ to_tsquery('simple', %s)
                OR EXISTS (
                    SELECT 1
                    FROM {attachment_table} AS search_attachment
                    WHERE search_attachment.content_type_id = %s
                      AND search_attachment.object_id = {userstory_table}.id
                      AND search_attachment.project_id = {userstory_table}.project_id
                      AND search_attachment.is_deprecated = FALSE
                      AND to_tsvector(
                          'simple',
                          coalesce(search_attachment.name, '') || ' ' ||
                          coalesce(search_attachment.description, '')
                      ) @@ to_tsquery('simple', %s)
                )
                OR EXISTS (
                    SELECT 1
                    FROM {task_table} AS search_task
                    WHERE search_task.user_story_id = {userstory_table}.id
                      AND search_task.project_id = {userstory_table}.project_id
                      AND EXISTS (
                          SELECT 1
                          FROM {attachment_table} AS search_task_attachment
                          WHERE search_task_attachment.content_type_id = %s
                            AND search_task_attachment.object_id = search_task.id
                            AND search_task_attachment.project_id = search_task.project_id
                            AND search_task_attachment.is_deprecated = FALSE
                            AND to_tsvector(
                                'simple',
                                coalesce(search_task_attachment.name, '') || ' ' ||
                                coalesce(search_task_attachment.description, '')
                            ) @@ to_tsquery('simple', %s)
                      )
                      AND {task_permission_clause}
                )
            )
        """.format(
            userstory_table=userstory_table,
            task_table=task_table,
            attachment_table=attachment_table,
            task_permission_clause=task_permission_clause,
        )

        return queryset.extra(
            where=[where_clause],
            params=[
                tsquery,
                userstory_content_type.pk,
                tsquery,
                task_content_type.pk,
                tsquery,
                *task_permission_params,
            ],
        )


class EpicFilter(filters.BaseRelatedFieldsFilter):
    filter_name = "epics"
    param_name = "epic"
    exclude_param_name = 'exclude_epic'


class SwimlanesFilter(filters.BaseRelatedFieldsFilter):
    filter_name = 'swimlane'
    param_name = "swimnlane"
    exclude_param_name = 'exclude_swimlane'


class UserStoryStatusesFilter(filters.StatusesFilter):
    def filter_queryset(self, request, queryset, view):
        project_id = None
        if "project" in request.QUERY_PARAMS:
            try:
                project_id = int(request.QUERY_PARAMS["project"])
            except ValueError:
                logger.error("Filtering user stories by status. Project value should be an integer: {}".format(
                    request.QUERY_PARAMS["project"]))
                raise exc.BadRequest(_("'project' must be an integer value."))

        if project_id:
            queryset = queryset.filter(status__project_id=project_id)

        return super().filter_queryset(request, queryset, view)


class AssignedUsersFilter(filters.BaseRelatedFieldsFilter):
    filter_name = 'assigned_users'
    exclude_param_name = 'exclude_assigned_users'

    def _get_queryparams(self, params, mode=''):
        param_name = self.exclude_param_name if mode == 'exclude' else self.param_name or \
                                                                       self.filter_name
        raw_value = params.get(param_name, None)
        if raw_value:
            value = self._prepare_filter_data(raw_value)
            UserStoryModel = apps.get_model("userstories", "UserStory")

            if None in value:
                value.remove(None)
                assigned_users_ids = UserStoryModel.objects.order_by().filter(
                    assigned_users__isnull=True,
                    id=OuterRef('pk')).values('pk')

                assigned_user_filter_none = Q(pk__in=Subquery(assigned_users_ids))
                assigned_to_filter_none = Q(assigned_to__isnull=True)

                return (get_assigned_users_filter(UserStoryModel, value)
                        | Q(assigned_user_filter_none, assigned_to_filter_none))
            else:
                return get_assigned_users_filter(UserStoryModel, value)

        return None


class UserStoriesRoleFilter(filters.BaseRelatedFieldsFilter):
    filter_name = "role_id"
    param_name = "role"
    exclude_param_name = 'exclude_role'

    def filter_queryset(self, request, queryset, view):
        Membership = apps.get_model('projects', 'Membership')

        operations = {
            "filter": self._prepare_filter_query,
            "exclude": self._prepare_exclude_query,
        }

        for mode, qs_method in operations.items():
            query = self._get_queryparams(request.QUERY_PARAMS, mode=mode)
            if query:
                memberships = Membership.objects.filter(query).exclude(user__isnull=True). \
                    values_list("user_id", flat=True)
                if memberships:
                    user_story_model = apps.get_model("userstories", "UserStory")
                    queryset = queryset.filter(
                        qs_method(Q(get_assigned_users_filter(user_story_model, memberships)))
                    )

        return filters.FilterBackend.filter_queryset(self, request, queryset, view)


class DashboardFilter(filters.FilterBackend):
    """
    This filter improves performance for dashboard queries.
    Only search in the user projects
    """
    filter_name = 'dashboard'
    param_name = "dashboard"

    def _filter_user_projects(self, request):
        membership_model = apps.get_model('projects', 'Membership')
        if isinstance(request.user, AnonymousUser):
            return None
        else:
            memberships_project_ids = membership_model.objects.filter(user=request.user).values(
                'project_id')

        return Subquery(memberships_project_ids)

    def filter_queryset(self, request, queryset, view):
        if request.QUERY_PARAMS.get(self.param_name, False):
            user_projects_ids_subquery = self._filter_user_projects(request)

            if user_projects_ids_subquery:
                queryset = queryset.filter(project_id__in=user_projects_ids_subquery)

        return super().filter_queryset(request, queryset, view)
