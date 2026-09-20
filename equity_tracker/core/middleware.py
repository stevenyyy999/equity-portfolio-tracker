from django.utils.cache import add_never_cache_headers

# Problem: When logged in, the user can log out, press the back arrow, and enter a
# page as if they are logged in.

# By default, our browsers would cache data such as login


class NoCacheAfterLogoutMiddleware:
    # Creates middleware object for Django and pass on a get_response callable w/ no inputs

    # get_response takes in a HttpRequest, outputs a HttpResponse
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if self._should_disable_cache(request):
            # mutates our http response, tells it to never cache our login data
            add_never_cache_headers(response)

        return response

    def _should_disable_cache(self, request):
        # try to grab the "user" attribute from the HttpRequest, if so, check if user is logged in
        user = getattr(request, "user", None)
        is_authenticated = bool(user and user.is_authenticated)

        # True if the user is logged in or is on a sensitive page (accounts)
        return is_authenticated or request.path.startswith("/accounts/")
