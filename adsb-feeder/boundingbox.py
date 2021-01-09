import jsonschema
import json
import sys


class BoundingBox(object):
    validKeys = ["min_latitude","max_latitude","min_longitude","max_longitude","min_altitude", "max_altitude"]

    def __init__(self, source=None):
        """
        default to a match-all bbox
        """
        self.min_latitude = -90
        self.max_latitude = 90
        self.min_longitude = -180
        self.max_longitude = 180
        self.min_altitude = -100
        self.max_altitude = 10000000
        if source:
            self.fromDict(source)

    def fromDict(self, d):
        for k, v in d.items():
            if k in self.validKeys:
                setattr(self, k, float(v))
        log.debug(f"new bbox = {repr(self)}")

    def fromParams(self, params):
        for k in self.validKeys:
            if k in params:
                try:
                    v = float(params[k][0])
                    setattr(self, k, v)
                    log.debug(f"set {k}={v} from {params}")
                except Exception as e:
                    log.info(f"parsing param {k} from {params} :  {e}")
                    pass
        log.debug(f"new bbox = {repr(self)}")

    def __repr__(self):
        return (
            f'BoundingBox({self.min_latitude}, {self.max_latitude},'
            f' {self.min_longitude}, {self.max_longitude},'
            f' {self.min_altitude}, {self.max_altitude})'
        )

class BBoxValidator(object):
    schema = {
        "type": "object",
        "properties": {
            "min_latitude": {
                "type": "number",
                # "minimum": -90,
                # "maximum": 90
            },
            "min_longitude": {
                "type": "number",
                # "minimum": -180,
                # "maximum": 180
            },
            "max_latitude": {
                "type": "number",
                # "minimum": -90,
                # "maximum": 90
            },
            "max_longitude": {
                "type": "number",
                # "minimum": -180,
                # "maximum": 180
            },
            "min_altitude": {
                "type": "number"
            },
            "max_altitude": {
                "type": "number"
            }
        },
        "required": ["min_latitude", "min_longitude", "max_latitude", "max_longitude"],
        "additionalProperties": {"type": "number"},
        "minProperties": 4,
        "maxProperties": 6
    }

    def __init__(self):
        self.v = jsonschema.Draft7Validator(self.schema)

    def validate_str(self, s):

        try:
            bb = json.loads(s)
            return self.validate(bb)
        except Exception:
            pass
        try:
            bb = json.loads(s.decode("ascii"))
            return self.validate(bb)
        except Exception as e:

            return (False, None, {
                "result": -1,
                "errors": f'JSON parse error: {e}'
            })

    def validate(self, instance):
        errors = sorted(self.v.iter_errors(instance), key=lambda e: e.path)
        if errors:
            return (False, None, {
                "result": -1,
                "errors": [e.message for e in errors]
            })
        bbox = BoundingBox(source=instance)
        return (True, bbox, None)


def main(argv):
    v = BBoxValidator()
    for filename in argv[1:]:
        instance = json.load(open(filename))
        (rc, bbox, response) = v.validate(instance)
        print(f'{filename}: rc={rc} bbox={bbox} response={response}')


if __name__ == "__main__":
    main(sys.argv)
