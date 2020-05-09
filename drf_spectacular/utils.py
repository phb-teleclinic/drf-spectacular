import inspect

from rest_framework.settings import api_settings


class PolymorphicProxySerializer:
    """
    This class is to be used with :func:`@extend_schema <.extend_schema>` to
    signal a request/response might be polymorphic (accepts/returns data
    possibly from different serializers)

    Beware that this is not a real serializer and therefore is not derived from
    serializers.Serializer. It *cannot* be used in views as `serializer_class`
    or as field in a actual serializer. You likely want to handle this in the
    view method.

    Also make sure that each sub-serializer has a field named after the value of
    `resource_type_field` (discriminator field) for the the mapping and parity
    with with the generated schema.
    """

    def __init__(self, component_name, serializers, resource_type_field_name):
        self.component_name = component_name
        self.serializers = serializers
        self.resource_type_field_name = resource_type_field_name


class OpenApiSchemaBase:
    pass


class OpenApiParameter(OpenApiSchemaBase):
    QUERY = 'query'
    PATH = 'path'
    HEADER = 'header'
    COOKIE = 'cookie'

    def __init__(self, name, type=str, location=QUERY, required=False, description='', enum=None, deprecated=False):
        self.name = name
        self.type = type
        self.location = location
        self.required = required
        self.description = description
        self.enum = enum
        self.deprecated = deprecated


def extend_schema(
        operation_id=None,
        parameters=None,
        request=None,
        responses=None,
        auth=None,
        description=None,
        deprecated=None,
        tags=None,
        exclude=False,
        operation=None,
        methods=None,
        versions=None,
):
    """
    decorator for the "view" kind. partially or completely overrides what would be
    generated by drf-spectacular.

    :param operation_id: replaces the auto-generated operation_id. make sure there
        are no naming collisions.
    :param parameters: list of additional or replacement parameters added to the
        auto-discovered fields.
    :param responses: replaces the discovered Serializer. Takes a variety of
        inputs that can be used individually or combined

        - ``Serializer`` class
        - ``Serializer`` instance (e.g. ``Serializer(many=True)`` for listings)
        - ``dict`` with status codes as keys and `Serializers` as values.
        - :class:`.PolymorphicProxySerializer` for signaling that
          the operation may yield data from different serializers depending
          on the circumstances.
    :param request: replaces the discovered ``Serializer``.
    :param auth:
    :param description: replaces discovered doc strings
    :param deprecated: mark operation as deprecated
    :param tags: override default list of tags
    :param exclude: set True to exclude operation from schema
    :param operation: manually override what auto-discovery would generate. you must
        provide a OpenAPI3-compliant dictionary that gets directly translated to YAML.
    :param methods: scope extend_schema to specific methods. matches all by default.
    :param versions: scope extend_schema to specific API version. matches all by default.
    :return:
    """
    def decorator(f):
        BaseSchema = (
            # explicit manually set schema
            getattr(f, 'schema', None)
            # previously set schema with @extend_schema on views methods
            or getattr(f, 'kwargs', {}).get('schema', None)
            # previously set schema with @extend_schema on @api_view
            or getattr(getattr(f, 'cls', None), 'kwargs', {}).get('schema', None)
            # the default
            or api_settings.DEFAULT_SCHEMA_CLASS
        )

        if not inspect.isclass(BaseSchema):
            BaseSchema = BaseSchema.__class__

        def is_in_scope(ext_schema):
            version, _ = ext_schema.view.determine_version(
                ext_schema.view.request,
                **ext_schema.view.kwargs
            )
            version_scope = versions is None or version in versions
            method_scope = methods is None or ext_schema.method in methods
            return method_scope and version_scope

        class ExtendedSchema(BaseSchema):
            def get_operation(self, path, path_regex, method, registry):
                self.method = method

                if exclude and is_in_scope(self):
                    return None
                if operation is not None and is_in_scope(self):
                    return operation
                return super().get_operation(path, path_regex, method, registry)

            def get_operation_id(self):
                if operation_id and is_in_scope(self):
                    return operation_id
                return super().get_operation_id()

            def get_override_parameters(self):
                if parameters and is_in_scope(self):
                    return parameters
                return super().get_override_parameters()

            def get_auth(self):
                if auth and is_in_scope(self):
                    return auth
                return super().get_auth()

            def get_request_serializer(self):
                if request and is_in_scope(self):
                    return request
                return super().get_request_serializer()

            def get_response_serializers(self):
                if responses and is_in_scope(self):
                    return responses
                return super().get_response_serializers()

            def get_description(self):
                if description and is_in_scope(self):
                    return description
                return super().get_description()

            def is_deprecated(self):
                if deprecated and is_in_scope(self):
                    return deprecated
                return super().is_deprecated()

            def get_tags(self):
                if tags is not None and is_in_scope(self):
                    return tags
                return super().get_tags()

        if inspect.isclass(f):
            class ExtendedView(f):
                schema = ExtendedSchema()
            return ExtendedView
        elif callable(f) and hasattr(f, 'cls'):
            # 'cls' attr signals that as_view() was called, which only applies to @api_view.
            # keep a "unused" schema reference at root level for multi annotation convenience.
            setattr(f.cls, 'kwargs', {'schema': ExtendedSchema})
            # set schema on method kwargs context to emulate regular view behaviour.
            for method in f.cls.http_method_names:
                setattr(getattr(f.cls, method), 'kwargs', {'schema': ExtendedSchema})
            return f
        elif callable(f):
            # custom actions have kwargs in their context, others don't. create it so our create_view
            # implementation can overwrite the default schema
            if not hasattr(f, 'kwargs'):
                f.kwargs = {}
            # this simulates what @action is actually doing. somewhere along the line in this process
            # the schema is picked up from kwargs and used. it's involved my dear friends.
            # use class instead of instance due to descriptor weakref reverse collisions
            f.kwargs['schema'] = ExtendedSchema
            return f
        else:
            return f

    return decorator


def extend_schema_field(field):
    """
    Decorator for the "field" kind. Can be used with ``SerializerMethodField`` (annotate the actual
    method) or with custom ``serializers.Field`` implementations.

    If your custom serializer field base class is already the desired type, decoration is not necessary.
    To override the discovered base class type, you can decorate your custom field class.

    Always takes precedence over other mechanisms (e.g. type hints, auto-discovery).

    :param field: accepts a ``Serializer`` or :class:`~.types.OpenApiTypes`
    """

    def decorator(f):
        if not hasattr(f, '_spectacular_annotation'):
            f._spectacular_annotation = {}
        f._spectacular_annotation['field'] = field
        return f

    return decorator


def extend_schema_serializer(many=None):
    """
    Decorator for the "serializer" kind. Intended for overriding default serializer behaviour that
    cannot be influenced through `.extend_schema`.

    :param many: override how serializer is initialized. Mainly used to coerce the list view detection
        heuristic to acknowledge a non-list serializer.
    """
    def decorator(klass):
        if not hasattr(klass, '_spectacular_annotation'):
            klass._spectacular_annotation = {}
        if many is not None:
            klass._spectacular_annotation['many'] = many
        return klass

    return decorator
