import jwt
import os
from datetime import datetime #timedelta, timezone,

_audience = ["adsb-geobuf", "adsb-json"]
_issuer = "urn:mah.priv.at"

class JWTAuthenticator(object):

    def __init__(self,
                 jwt_secret=None,
                 issuer=_issuer,
                 audience=_audience,
                 algorithm="HS256"):

        if jwt_secret is None:
            self.jwt_secret = os.environ.get("JWT_SECRET")
        else:
            self.jwt_secret = jwt_secret
        if not self.jwt_secret:
	           raise Exception("jwt secret missing")

        self.jwt = None
        self.issuer = issuer
        self.algorithm = algorithm
        self.audience = audience

    def genToken(self, user="demo",
                expiresIn=900,
                reuseIn=0,
                expiresOn="2099-01-01 00:00:00 +0000"):
        token = {
            "usr" : user,
            "dur" : expiresIn,
            "exp" : datetime.strptime(expiresOn, "%Y-%m-%d %H:%M:%S %z").timestamp(),
            "iss" : self.issuer,
            "aud" : self.audience,
            "iat" : datetime.utcnow(),
            "rui" : reuseIn,
        }
        encoded = jwt.encode( token, self.jwt_secret, algorithm="HS256")
        return encoded

    def decodeToken(self, token, **kwargs):
        return jwt.decode(token, self.jwt_secret, audience=self.audience, algorithms=[self.algorithm], **kwargs)

        # raises InvalidAudienceError("Invalid audience")
        # raises  ExpiredSignatureError("Signature has expired")

if __name__ == "__main__":


    jwt_auth = JWTAuthenticator(audience=_audience,
                                issuer=_issuer,
                                algorithm="HS256")

    token = jwt_auth.genToken(user="github",
                            expiresIn=300,
                            reuseIn=30,
                            expiresOn="2022-06-01 00:00:00 +0000")

    print("encoded token:",  token)
    decoded = jwt_auth.decodeToken(token)
    print("encoded token:", decoded)
    print("expires on:", datetime.fromtimestamp(decoded["exp"]))
    print("issued at:", datetime.fromtimestamp(decoded["iat"]))
