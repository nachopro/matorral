from itertools import groupby

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.generic import ListView
from django.views.generic.detail import DetailView
from django.views.generic.edit import CreateView, UpdateView
from rest_framework import viewsets

from ..utils import get_clean_next_url
from .forms import SprintGroupByForm
from .models import Sprint
from .serializers import SprintSerializer
from .tasks import duplicate_sprints, remove_sprints, reset_sprint


class SprintDetailView(DetailView):

    model = Sprint

    def get_children(self):
        queryset = self.get_object().story_set.select_related('requester', 'assignee', 'epic', 'state')

        config = dict(
            epic=('epic__name', lambda story: story.epic and story.epic.title or 'No Epic'),
            state=('state__slug', lambda story: story.state.name),
            requester=('requester__username', lambda story: story.requester and story.requester.username or 'Unset'),
            assignee=('assignee__username', lambda story: story.assignee and story.assignee.username or 'Unassigned'),
        )

        group_by = self.request.GET.get('group_by')

        try:
            order_by, fx = config[group_by]
        except KeyError:
            return [(None, queryset)]
        else:
            queryset = queryset.order_by(order_by)
            foo = [(t[0], list(t[1])) for t in groupby(queryset, key=fx)]
            return foo

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['group_by_form'] = SprintGroupByForm(self.request.GET)
        context['objects_by_group'] = self.get_children()
        context['group_by'] = self.request.GET.get('group_by')
        return context

    def post(self, *args, **kwargs):
        params = self.request.POST.dict()
        url = self.request.get_full_path()

        if params.get('remove') == 'yes':
            remove_sprints.delay([self.get_object().id])
            url = reverse_lazy('sprints:sprint-list')

        elif params.get('sprint-reset') == 'yes':
            story_ids = [t[6:] for t in params.keys() if 'story-' in t]
            reset_sprint.delay(story_ids)

        if self.request.META.get('HTTP_X_FETCH') == 'true':
            return JsonResponse(dict(url=url))
        else:
            return HttpResponseRedirect(url)


class SprintViewSet(viewsets.ModelViewSet):
    serializer_class = SprintSerializer
    queryset = Sprint.objects.all()


class BaseListView(ListView):
    paginate_by = 10

    filter_fields = {}
    select_related = None
    prefetch_related = None

    def _build_filters(self, q):
        params = {}

        for part in (q or '').split():
            if ":" in part:
                field, value = part.split(':')
                try:
                    operator = self.filter_fields[field]
                    params[operator] = value
                except KeyError:
                    continue
            else:
                params['title__icontains'] = part

        return params

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        if self.request.GET.get('q') is not None:
            context['show_all_url'] = self.request.path

        context['title'] = self.model._meta.verbose_name_plural.capitalize()
        context['singular_title'] = self.model._meta.verbose_name.capitalize()

        return context

    def get_queryset(self):
        qs = self.model.objects

        q = self.request.GET.get('q')

        params = self._build_filters(q)

        if q is None:
            qs = qs.all()
        else:
            qs = qs.filter(**params)

        if self.select_related is not None:
            qs = qs.select_related(*self.select_related)

        if self.prefetch_related is not None:
            qs = qs.prefetch_related(*self.prefetch_related)

        return qs


@method_decorator(login_required, name='dispatch')
class SprintList(BaseListView):
    model = Sprint
    filter_fields = {}
    select_related = None
    prefetch_related = None

    def post(self, *args, **kwargs):
        params = self.request.POST.dict()

        sprint_ids = [t[7:] for t in params.keys() if 'sprint-' in t]

        if len(sprint_ids) > 0:
            if params.get('remove') == 'yes':
                remove_sprints.delay(sprint_ids)

            if params.get('duplicate') == 'yes':
                duplicate_sprints.delay(sprint_ids)

        url = self.request.get_full_path()

        if self.request.META.get('HTTP_X_FETCH') == 'true':
            return JsonResponse(dict(url=url))
        else:
            return HttpResponseRedirect(url)


class SprintBaseView(object):
    model = Sprint
    fields = [
        'title', 'description', 'starts_at', 'ends_at'
    ]

    @property
    def success_url(self):
        return get_clean_next_url(self.request, reverse_lazy('sprints:sprint-list'))


@method_decorator(login_required, name='dispatch')
class SprintCreateView(SprintBaseView, CreateView):
    pass


@method_decorator(login_required, name='dispatch')
class SprintUpdateView(SprintBaseView, UpdateView):
    pass
