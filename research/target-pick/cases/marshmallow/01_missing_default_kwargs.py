from marshmallow import Schema, fields
class S(Schema):
    name = fields.String(missing="anon")   # 4.x: load_default=
print("load {} ->", S().load({}))
