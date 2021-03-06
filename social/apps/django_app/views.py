from urllib2 import quote

from django.http import HttpResponseRedirect, HttpResponse
from django.contrib.auth import login, REDIRECT_FIELD_NAME, BACKEND_SESSION_KEY
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_POST

from social.utils import sanitize_redirect, user_is_authenticated, \
                         user_is_active
from social.apps.django_app.utils import strategy, setting, BackendWrapper


DEFAULT_REDIRECT = setting('LOGIN_REDIRECT_URL')
LOGIN_ERROR_URL = setting('LOGIN_ERROR_URL', setting('LOGIN_URL'))


@strategy('social:complete')
def auth(request, backend):
    # Save any defined next value into session
    data = request.POST if request.method == 'POST' else request.GET

    # Save extra data into session.
    for field_name in request.strategy.setting('FIELDS_STORED_IN_SESSION', []):
        if field_name in data:
            request.session[field_name] = data[field_name]

    if REDIRECT_FIELD_NAME in data:
        # Check and sanitize a user-defined GET/POST next field value
        redirect = data[REDIRECT_FIELD_NAME]
        if setting('SANITIZE_REDIRECTS', True):
            redirect = sanitize_redirect(request.get_host(), redirect)
        request.session[REDIRECT_FIELD_NAME] = redirect or DEFAULT_REDIRECT
    return request.strategy.start()


@csrf_exempt
@strategy('social:complete')
def complete(request, backend, *args, **kwargs):
    """Authentication complete view, override this view if transaction
    management doesn't suit your needs."""
    strategy = request.strategy
    # pop redirect value before the session is trashed on login()
    redirect_value = request.session.get(REDIRECT_FIELD_NAME, '') or \
                     request.REQUEST.get(REDIRECT_FIELD_NAME, '')

    is_authenticated = user_is_authenticated(request.user)
    user = is_authenticated and request.user or None
    url = DEFAULT_REDIRECT

    if request.session.get('partial_pipeline'):
        data = request.session.pop('partial_pipeline')
        kwargs = kwargs.copy()
        kwargs.setdefault('user', user)
        idx, xargs, xkwargs = strategy.from_session(data, request=request,
                                                    *args, **kwargs)
        if xkwargs.get('backend', '') == backend:
            user = strategy.continue_pipeline(pipeline_index=idx,
                                              *xargs, **xkwargs)
        else:
            strategy.clean_partial_pipeline()
            user = strategy.complete(user=user, request=request,
                                     *args, **kwargs)
    else:
        user = strategy.complete(user=user, request=request,
                                 *args, **kwargs)

    if isinstance(user, HttpResponse):
        return user

    if is_authenticated:
        if not user:
            url = redirect_value or DEFAULT_REDIRECT
        else:
            url = redirect_value or \
                  strategy.setting('NEW_ASSOCIATION_REDIRECT_URL') or \
                  DEFAULT_REDIRECT
    elif user:
        if user_is_active(user):
            # catch is_new flag before login() resets the instance
            is_new = getattr(user, 'is_new', False)
            login(request, user)
            # Hack django.auth backend loading since they create an instance
            # that won't know about the strategy/storage layout being used
            request.session['original_' + BACKEND_SESSION_KEY] = \
                    request.session[BACKEND_SESSION_KEY]
            request.session[BACKEND_SESSION_KEY] = '%s.%s' % (
                BackendWrapper.__module__,
                BackendWrapper.__name__
            )
            # user.social_user is the used UserSocialAuth instance defined
            # in authenticate process
            social_user = user.social_user

            if setting('SESSION_EXPIRATION', True):
                # Set session expiration date if present and not disabled
                # by setting. Use last social-auth instance for current
                # provider, users can associate several accounts with
                # a same provider.
                expiration = social_user.expiration_datetime()
                if expiration:
                    try:
                        request.session.set_expiry(expiration)
                    except OverflowError:
                        # Handle django time zone overflow
                        request.session.set_expiry(None)

            # store last login backend name in session
            request.session['social_auth_last_login_backend'] = \
                    social_user.provider

            # Remove possible redirect URL from session, if this is a new
            # account, send him to the new-users-page if defined.
            new_user_redirect = strategy.setting('NEW_USER_REDIRECT_URL')
            if new_user_redirect and is_new:
                url = new_user_redirect
            else:
                url = redirect_value or strategy.setting('LOGIN_REDIRECT_URL')
        else:
            url = strategy.setting('INACTIVE_USER_URL', LOGIN_ERROR_URL)
    else:
        url = strategy.setting('LOGIN_ERROR_URL', LOGIN_ERROR_URL)

    if redirect_value and redirect_value != url:
        redirect_value = quote(redirect_value)
        url += ('?' in url and '&' or '?') + \
               '%s=%s' % (REDIRECT_FIELD_NAME, redirect_value)
    return HttpResponseRedirect(url)


@login_required
@strategy()
@require_POST
@csrf_protect
def disconnect(request, backend, association_id=None):
    """Disconnects given backend from current logged in user."""
    strategy = request.strategy
    strategy.disconnect(user=request.user, association_id=association_id)
    url = request.REQUEST.get(REDIRECT_FIELD_NAME, '') or \
          strategy.setting('DISCONNECT_REDIRECT_URL') or \
          DEFAULT_REDIRECT
    return HttpResponseRedirect(url)
