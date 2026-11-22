-- activity_group is the author's ASSUMPTION about how the Open University's activity types group together.
select activity_type,
       case
           when activity_type in ('oucontent', 'resource', 'subpage', 'url', 'page', 'folder', 'dualpane',
                                  'htmlactivity', 'sharedsubpage', 'glossary', 'dataplus') then 'Content'
           when activity_type in ('forumng', 'oucollaborate', 'ouelluminate', 'ouwiki') then 'Communication'
           when activity_type in ('quiz', 'externalquiz', 'questionnaire', 'repeatactivity') then 'Assessment practice'
           when activity_type = 'homepage' then 'Homepage'
           else 'Other'
       end as activity_group,
       count(*) as n_sites
from silver.vle_sites
group by activity_type
