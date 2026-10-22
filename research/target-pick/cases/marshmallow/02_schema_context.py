from marshmallow import Schema, fields
class S(Schema):
    greeting = fields.Method("hello")
    def hello(self, obj):
        return f"hi {self.context['user']}"  # context removed in 4 (contextvars instead)
print("context ->", S(context={"user": "ann"}).dump({}))
