"""EC-015: AI-use policy detection, course-wide scope, exceptions, conflicts and enforcement.

Synthetic fixtures, fake providers and temporary SQLite only; no model decides policy.
"""

import unittest

from backend.app import rag_service
from backend.app.ai_use_policy import LIMITED, PERMITTED, PROHIBITED, ai_statements, work_types
from backend.app.assignment_policy_service import (
    ASSIGNMENT_RESTRICTS, CONFLICT_NOTICE, COURSE_RESTRICTS, restriction_kinds, work_request,
)
from backend.app.chunking_service import ensure_course_chunks
from backend.app.conversation_service import ConversationService
from backend.app.embedding_service import embed_course
from backend.app.query_understanding_service import empty_understanding
from backend.evaluation.fixtures import EvaluationFixture, ReplayProvider

COURSE_BAN = "Course policies. Use of generative AI tools is prohibited in this course. Late work loses 10 percent."
DELEGATING_BAN = "Generative AI is prohibited unless explicitly permitted by the assignment instructions."
PERMISSION = "Analyze findLast and countPositive. You may use AI tools for this assignment."

# The ten clear prohibitions the previous detector missed (4 of 14 were recognized).
PREVIOUSLY_MISSED = (
    "You are not allowed to use AI tools for this assignment.",
    "You are not permitted to use generative AI on this assignment.",
    "Don't use ChatGPT for this homework.",
    "You should not use AI tools to write your answers.",
    "AI tools are banned for this assignment.",
    "This assignment must be completed without the use of AI.",
    "Using ChatGPT is considered academic dishonesty.",
    "Submitting AI-generated code will be treated as cheating.",
    "ChatGPT, Copilot and similar tools are off limits.",
    "You must not use GitHub Copilot or Claude.",
)
PREVIOUSLY_RECOGNIZED = (
    "The use of ChatGPT or other AI tools is not allowed.",
    "Students may not use ChatGPT on this assignment.",
    "Do not use AI to complete this homework.",
    "Use of generative AI is prohibited.",
)


def kinds(text):
    return sorted({statement['kind'] for statement in ai_statements(text)})


class DetectionTests(unittest.TestCase):
    def test_all_fourteen_reported_prohibitions_are_recognized(self):
        for text in PREVIOUSLY_MISSED + PREVIOUSLY_RECOGNIZED:
            with self.subTest(text=text):
                self.assertEqual(restriction_kinds(text), [PROHIBITED])

    def test_other_common_prohibition_wording(self):
        for text in ("Don’t use ChatGPT for this homework.", "AI-generated code will be treated as cheating.",
                     "A.I. tools may not be used in this course.", "Students are prohibited from using large language models.",
                     "Do not copy, share, or use ChatGPT.", "Any work found to be AI-generated will receive a zero.",
                     "Use of AI tools will result in a failing grade.", "Cheating includes using ChatGPT to write your code.",
                     "Never use ChatGPT for graded work.", "Please refrain from using AI assistants.",
                     "Do not have ChatGPT write your essay.", "(No AI tools allowed)", "AI is never allowed in this class.",
                     "Do not use online tools such as ChatGPT.", "No AI."):
            with self.subTest(text=text):
                self.assertEqual(kinds(text), [PROHIBITED])

    def test_permissions_are_not_prohibitions(self):
        for text in ("You may use AI tools.", "AI use is not prohibited in this course.", "Feel free to use ChatGPT for this assignment.",
                     "Students are encouraged to use generative AI.", "AI is allowed; you must not plagiarize.",
                     "This course permits the use of generative AI as a resource for completing assignments.",
                     "The instructor allows students to use ChatGPT."):
            with self.subTest(text=text):
                self.assertEqual(kinds(text), [PERMITTED])
                self.assertEqual(restriction_kinds(text), [])

    def test_institutional_wording_is_recognized(self):
        for text in ("This course prohibits the use of generative AI.", "The instructor does not permit students to use ChatGPT."):
            with self.subTest(text=text):
                self.assertEqual(kinds(text), [PROHIBITED])

    def test_citation_rules_and_conditions_are_neither_bans_nor_permissions(self):
        for text in ("Using AI without citation is considered plagiarism.", "When AI use is authorized, students must cite it.",
                     "Permission to use AI for one assignment does not provide permission to use it for another assignment."):
            with self.subTest(text=text):
                self.assertEqual(kinds(text), [])

    def test_rule_about_unauthorized_use_defers_to_authorization(self):
        for text in ("Unauthorized use of generative AI could result in breaches of academic integrity.",
                     "Unauthorized or unapproved use of generative AI will be subject to disciplinary procedures.",
                     "Unauthorized, undisclosed, or misrepresented use of generative AI may be treated as a violation of academic integrity.",
                     "Unless the instructor provides explicit written authorization for a particular assignment, students may not use AI tools to:",
                     "AI tools may be used in this course only as learning-support tools and only in ways permitted by the instructor."):
            with self.subTest(text=text):
                statements = ai_statements(text)
                self.assertEqual(len(statements), 1)
                self.assertTrue(statements[0]['allows_exceptions'])
                self.assertTrue(restriction_kinds(text))

    def test_sentences_wrapped_across_lines_are_read_whole(self):
        wrapped = ("Course Policies\nThe use of generative\nAI tools such as ChatGPT is strictly\nprohibited in this course.\n"
                   "Late Work\nLate work loses 10 percent.")
        self.assertEqual(restriction_kinds(wrapped), [PROHIBITED])
        long_line = "Students in this section should remember that the use of generative artificial intelligence and\nChatGPT is prohibited."
        self.assertEqual(restriction_kinds(long_line), [PROHIBITED])
        # A short heading is not merged into the next line to invent a rule.
        self.assertEqual(restriction_kinds("Generative AI\nNot permitted: late submissions"), [])

    def test_limited_permission_is_a_restriction_on_everything_else(self):
        for text in ("Use of AI tools is permitted for brainstorming only.", "You may only use AI to check grammar.",
                     "ChatGPT may be used with prior instructor approval.", "You may use AI to brainstorm ideas.",
                     "Feel free to use ChatGPT to check your grammar."):
            with self.subTest(text=text):
                self.assertEqual(restriction_kinds(text), [LIMITED])

    def test_ai_as_a_course_topic_or_unrelated_rule_is_not_a_policy(self):
        for text in ("This course covers artificial intelligence research and AI ethics.", "No AI background is required.",
                     "Do not use your phone in the AI lab.", "Late work is not allowed.", "You do not need to use AI.",
                     "This AI course treats plagiarism seriously: copying is cheating.",
                     "Read the linked questions and AI-use policy.", "Submit only a PDF. It must contain your name."):
            with self.subTest(text=text):
                self.assertEqual(kinds(text), [])

    def test_a_permission_does_not_absorb_a_separate_prohibition(self):
        self.assertEqual(kinds("AI tools may be used and collaboration is prohibited."), [PERMITTED])
        self.assertEqual(kinds("You may use AI for brainstorming but may not use it to write code."), sorted([LIMITED, PROHIBITED]))
        # "it" in the next sentence still refers to the tool named just before.
        self.assertEqual(restriction_kinds("You may use ChatGPT on this lab. You may not use it to write code."), [PROHIBITED])
        self.assertEqual(kinds("You may not use it after the deadline."), [])

    def test_statement_records_authorized_exceptions_and_work_scope(self):
        delegating = ai_statements(DELEGATING_BAN)[0]
        self.assertTrue(delegating['allows_exceptions'])
        self.assertFalse(ai_statements("Use of generative AI is prohibited.")[0]['allows_exceptions'])
        self.assertTrue(ai_statements("Unauthorized use of AI is prohibited.")[0]['allows_exceptions'])
        self.assertEqual(ai_statements("AI tools are prohibited on exams and quizzes.")[0]['work_types'], ['exam', 'quiz'])
        self.assertEqual(ai_statements("AI tools are prohibited on all assignments, including homework and exams.")[0]['work_types'], [])
        self.assertEqual(ai_statements("Do not use AI to write test cases.")[0]['work_types'], [])

    def test_work_types_come_from_real_titles(self):
        self.assertEqual(work_types("HW6_questions.pdf"), {'homework'})
        self.assertEqual(work_types("Midterm Exam 1"), {'exam'})
        self.assertEqual(work_types("Final Project"), {'project'})
        self.assertEqual(work_types("Lecture3.pptx"), set())

    def test_work_request_recognizes_plain_requests_to_do_the_work(self):
        for question in ("Write my essay for Project 1", "Do my homework 3", "Write the code for HW 3", "Complete this assignment for me"):
            with self.subTest(question=question):
                self.assertTrue(work_request(question))
        for question in ("How do I write the report?", "Explain HW 3", "Explain HW6 without giving me the answer",
                         "What does question 2 ask?"):
            with self.subTest(question=question):
                self.assertFalse(work_request(question))


class PolicyFixture:
    def setUp(self):
        self.fixture = EvaluationFixture()
        self.addCleanup(self.fixture.close)
        self.store = self.fixture.store

    def syllabus(self, text, **extra):
        self.fixture.save('syllabus:42', 'Syllabus', text, source_type='syllabus', **extra)
        ensure_course_chunks(42, self.store)
        embed_course(42, store=self.store, provider=self.fixture.embedding)

    def describe(self, canvas_id, description):
        next(a for a in self.fixture.assignments if a['canvas_id'] == canvas_id)['metadata']['description'] = description
        self.store.save_course(self.fixture.snapshot)

    def plan(self, question, **fields):
        fields.setdefault('intent', 'solve_question')
        return empty_understanding(question, **fields)

    def ask(self, question='Solve Homework 1 for me', *, unsafe=False, **fields):
        if 'document_reference' not in fields:
            fields.setdefault('assignment_reference', 'HW 1')
        provider = ReplayProvider(self.plan(question, **fields), unsafe=unsafe)
        result = rag_service.answer_question(42, question, store=self.store, embedding_provider=self.fixture.embedding, llm_provider=provider)
        return result, provider

    @staticmethod
    def cited(result):
        return {origin['document_key'] for source in result['sources'] for origin in source['origins']}

    @staticmethod
    def rules(result):
        return {(rule['kind'], rule['scope']) for rule in (result.get('assignment_policy') or {}).get('rules', [])}


class CourseWidePolicyTests(PolicyFixture, unittest.TestCase):
    def test_syllabus_ban_applies_to_every_assignment_before_generation(self):
        self.syllabus(COURSE_BAN)
        for question, reference in (('Solve Homework 1 for me', 'HW 1'), ('Solve Homework 10 for me', 'HW 10')):
            with self.subTest(question=question):
                result, provider = self.ask(question, assignment_reference=reference)
                self.assertTrue(result['policy_enforced'])
                self.assertFalse(provider.contexts)              # enforced before any answer generation
                self.assertEqual(self.rules(result), {(PROHIBITED, 'course')})
                self.assertIn('course syllabus prohibits', result['answer'])
                self.assertIn('cannot provide', result['answer'])
                self.assertNotIn('closely matches', result['answer'])        # the student named this assignment
                self.assertEqual(self.cited(result), {'syllabus:42'})

    def test_previously_missed_wording_is_enforced_from_the_syllabus(self):
        self.syllabus("Don't use ChatGPT for graded work. AI-generated code will be treated as cheating.")
        result, provider = self.ask()
        self.assertTrue(result['policy_enforced'])
        self.assertFalse(provider.contexts)

    def test_assignment_only_ban_does_not_spread_to_other_assignments(self):
        restricted, restricted_provider = self.ask('Solve Homework 6 for me', assignment_reference='HW 6')
        self.assertTrue(restricted['policy_enforced'])
        self.assertFalse(restricted_provider.contexts)
        self.assertEqual({scope for _, scope in self.rules(restricted)}, {'assignment'})
        other, provider = self.ask()
        self.assertNotIn('policy_enforced', other)
        self.assertEqual(self.rules(other), set())
        self.assertTrue(provider.contexts)

    def test_syllabus_rule_limited_to_exams_does_not_restrict_homework(self):
        self.syllabus("AI tools are prohibited on exams.")
        homework, provider = self.ask()
        self.assertNotIn('policy_enforced', homework)
        self.assertTrue(provider.contexts)
        self.fixture.assignments.append({'canvas_id': 301, 'course_id': 42, 'source_type': 'assignment', 'title': 'Midterm Exam',
                                         'url': 'https://canvas.example/courses/42/assignments/301',
                                         'metadata': {'id': 301, 'course_id': 42, 'name': 'Midterm Exam', 'description': 'Answer all four problems.'}})
        self.store.save_course(self.fixture.snapshot)
        self.fixture.save('assignment:301', 'Midterm Exam', 'Answer all four problems.', source_type='assignment')
        ensure_course_chunks(42, self.store)
        embed_course(42, store=self.store, provider=self.fixture.embedding)
        exam, exam_provider = self.ask('Solve the Midterm Exam for me', assignment_reference='Midterm Exam')
        self.assertTrue(exam['policy_enforced'])
        self.assertFalse(exam_provider.contexts)

    def test_course_ban_does_not_block_concepts_or_lecture_material(self):
        self.syllabus(COURSE_BAN)
        concept, provider = self.ask('What is matrix multiplication?', intent='course_wide_question', assignment_reference='')
        self.assertNotIn('assignment_policy', concept)
        self.assertTrue(provider.contexts)
        practice, practice_provider = self.ask('Solve Practice Questions 1.pdf', document_reference='Practice Questions 1.pdf')
        self.assertNotIn('policy_enforced', practice)
        self.assertTrue(practice_provider.contexts)

    def test_course_ban_covers_named_work_files_without_a_canvas_assignment(self):
        self.syllabus(COURSE_BAN)
        self.fixture.save('file:800', 'Project_Specification.pdf', 'Build a scheduler simulator and write a design report.')
        ensure_course_chunks(42, self.store)
        embed_course(42, store=self.store, provider=self.fixture.embedding)
        result, provider = self.ask('Write the code for Project_Specification.pdf', document_reference='Project_Specification.pdf')
        self.assertTrue(result['policy_enforced'])
        self.assertFalse(provider.contexts)

    def test_explanation_under_a_course_ban_is_generated_but_leak_checked(self):
        self.syllabus(COURSE_BAN)
        safe, provider = self.ask('Explain Homework 1', intent='explain_assignment')
        self.assertTrue(provider.contexts)                       # permissible help is still generated
        self.assertNotIn('policy_enforced', safe)
        leaked, _ = self.ask('Explain Homework 1', intent='explain_assignment', unsafe=True)
        self.assertTrue(leaked['policy_enforced'])               # a solution in the draft is replaced
        self.assertNotIn('def solve', leaked['answer'])

    def test_plain_request_to_do_the_work_is_blocked_even_if_the_model_says_explain(self):
        self.syllabus(COURSE_BAN)
        result, provider = self.ask('Write the code for Homework 1', intent='explain_assignment')
        self.assertTrue(result['policy_enforced'])
        self.assertFalse(provider.contexts)

    def test_instructor_template_syllabus_text_is_not_policy(self):
        self.syllabus("Verify your syllabus against the syllabus template. Use of generative AI tools is prohibited in this course.")
        result, provider = self.ask()
        self.assertNotIn('policy_enforced', result)
        self.assertTrue(provider.contexts)


class DeferringPolicyTests(PolicyFixture, unittest.TestCase):
    """A rule about *unauthorized* use is satisfied by the authorization the instructor states."""

    DEFERS = ("Generative AI Use in This Course\nThe use of generative AI in course assignments must align with the guidelines "
              "established by the instructor. Unauthorized use of generative AI could result in breaches of academic integrity. ")

    def test_syllabus_that_defers_and_then_permits_ai_is_not_a_ban(self):
        self.syllabus(self.DEFERS + "As the instructor, I have adopted the following policy. This course permits the use of generative AI "
                      "as a resource for completing assignments. However, students are required to cite any tools they use.")
        result, provider = self.ask()
        self.assertNotIn('policy_enforced', result)
        self.assertEqual(self.rules(result), set())
        self.assertTrue(provider.contexts)

    def test_syllabus_that_only_defers_restricts_until_an_authorization_is_found(self):
        self.syllabus(self.DEFERS)
        result, provider = self.ask()
        self.assertTrue(result['policy_enforced'])
        self.assertFalse(provider.contexts)
        self.assertIn('unless the instructor authorizes it', result['answer'])
        self.assertNotIn('syllabus prohibits', result['answer'])     # say what the rule says, not more
        self.describe(101, PERMISSION)                               # the assignment supplies the authorization
        authorized, authorized_provider = self.ask()
        self.assertNotIn('policy_enforced', authorized)
        self.assertTrue(authorized_provider.contexts)

    def test_flat_ban_is_not_lifted_by_a_permission_elsewhere_in_the_syllabus(self):
        self.syllabus("Use of generative AI tools is prohibited in this course. You may use AI tools to study for exams.")
        result, provider = self.ask()
        self.assertTrue(result['policy_enforced'])
        self.assertFalse(provider.contexts)

    def test_learning_support_only_syllabus_blocks_solutions_but_allows_an_assignment_exception(self):
        self.syllabus("AI tools may be used in this course only as general learning-support tools and only in ways permitted by the instructor.\n"
                      "Unless the instructor provides explicit written authorization for a particular\nassignment, students may not use AI tools to:\n"
                      "- Generate all or part of a graded assignment.\n- Generate answers to homework or lab questions.")
        blocked, provider = self.ask()
        self.assertTrue(blocked['policy_enforced'])
        self.assertFalse(provider.contexts)
        self.assertEqual({kind for kind, _ in self.rules(blocked)}, {PROHIBITED, LIMITED})
        self.describe(101, PERMISSION)
        allowed, allowed_provider = self.ask()
        self.assertNotIn('policy_enforced', allowed)
        self.assertEqual(len(allowed['assignment_policy']['exceptions']), 1)
        self.assertTrue(allowed_provider.contexts)


class ExceptionAndConflictTests(PolicyFixture, unittest.TestCase):
    def test_assignment_permission_is_an_authorized_exception_when_the_syllabus_delegates(self):
        self.syllabus(DELEGATING_BAN)
        self.describe(101, PERMISSION)
        result, provider = self.ask()
        self.assertNotIn('policy_enforced', result)
        self.assertTrue(provider.contexts)
        policy = result['assignment_policy']
        self.assertEqual((policy['rules'], len(policy['exceptions'])), ([], 1))
        # Without an assignment-level permission the delegating ban still applies.
        other, other_provider = self.ask('Solve Homework 10 for me', assignment_reference='HW 10')
        self.assertTrue(other['policy_enforced'])
        self.assertFalse(other_provider.contexts)

    def test_conflict_applies_the_course_ban_and_explains_it(self):
        self.syllabus(COURSE_BAN)
        self.describe(101, PERMISSION)
        result, provider = self.ask()
        self.assertTrue(result['policy_enforced'])
        self.assertFalse(provider.contexts)                      # permission is never assumed
        self.assertEqual([c['direction'] for c in result['assignment_policy']['conflicts']], [COURSE_RESTRICTS])
        self.assertIn('conflict', result['answer'])
        self.assertIn('until your instructor clarifies', result['answer'])
        self.assertEqual(self.cited(result), {'syllabus:42', 'assignment:101'})
        self.assertIn(CONFLICT_NOTICE, result['warnings'])

    def test_conflict_is_explained_on_permissible_answers_too(self):
        self.syllabus(COURSE_BAN)
        self.describe(101, PERMISSION)
        result, provider = self.ask('Explain Homework 1', intent='explain_assignment')
        self.assertTrue(provider.contexts)
        self.assertIn(CONFLICT_NOTICE, result['warnings'])
        self.assertEqual(result['warnings'].count(CONFLICT_NOTICE), 1)

    def test_assignment_ban_stands_when_the_syllabus_permits_ai(self):
        self.syllabus("You may use AI tools in this course.")
        result, provider = self.ask('Solve Homework 6 for me', assignment_reference='HW 6')
        self.assertTrue(result['policy_enforced'])
        self.assertFalse(provider.contexts)
        self.assertEqual([c['direction'] for c in result['assignment_policy']['conflicts']], [ASSIGNMENT_RESTRICTS])
        self.assertIn('assignment instructions prohibit', result['answer'])
        self.assertIn("this assignment's own restriction applies", result['answer'])
        # The permissive syllabus does not restrict, or excuse, any other assignment.
        other, other_provider = self.ask()
        self.assertNotIn('policy_enforced', other)
        self.assertTrue(other_provider.contexts)

    def test_limited_permission_blocks_solutions(self):
        self.describe(101, "Analyze findLast. Use of AI tools is permitted for brainstorming only.")
        result, provider = self.ask()
        self.assertTrue(result['policy_enforced'])
        self.assertFalse(provider.contexts)
        self.assertIn('only for limited purposes', result['answer'])


class RewordedRequestTests(PolicyFixture, unittest.TestCase):
    """A restriction follows the work the answer would come from, not the wording of the request."""

    def scheduling_homework(self, description="Compute the process waiting time for each scheduling policy."):
        # The fake embedding matches "process" text only to this assignment, so it is the best evidence.
        self.fixture.assignments.append({'canvas_id': 102, 'course_id': 42, 'source_type': 'assignment', 'title': 'HW 2',
                                         'url': 'https://canvas.example/courses/42/assignments/102',
                                         'metadata': {'id': 102, 'course_id': 42, 'name': 'HW 2', 'description': description}})
        self.store.save_course(self.fixture.snapshot)
        self.fixture.save('assignment:102', 'HW 2', description, source_type='assignment')
        ensure_course_chunks(42, self.store)
        embed_course(42, store=self.store, provider=self.fixture.embedding)

    def paste(self, question="Solve this: compute the process waiting time for FCFS", **fields):
        return self.ask(question, assignment_reference='', **fields)

    def test_pasted_assignment_question_inherits_the_course_ban(self):
        self.scheduling_homework()
        self.syllabus(COURSE_BAN)
        result, provider = self.paste()
        self.assertEqual(result['retrieval_route'], 'course_material')       # no assignment was named
        self.assertTrue(result['policy_enforced'])
        self.assertIn('closely matches assigned work', result['answer'])     # say why a rule applies
        self.assertFalse(provider.contexts)
        self.assertEqual(self.cited(result), {'syllabus:42'})

    def test_assigned_work_outranked_by_a_lecture_slide_still_decides(self):
        # Live finding: a slide quoting the same example scored above the homework file.
        from backend.app import retrieval_router
        self.scheduling_homework()
        self.syllabus(COURSE_BAN)
        self.fixture.save('file:820', 'Lecture 5 scheduling.pptx', 'Worked example: the process waiting time under FCFS.')
        ensure_course_chunks(42, self.store)
        embed_course(42, store=self.store, provider=self.fixture.embedding)
        question = "Solve this: compute the process waiting time for FCFS"
        route = retrieval_router._route_query(42, question, self.plan(question), store=self.store, provider=self.fixture.embedding)
        scored = [item for item in route['results'] if isinstance(item.get('score'), (int, float))]
        lecture = next(item for item in scored if item['sources'][0]['document_key'] == 'file:820')
        homework = next(item for item in scored if item['sources'][0]['document_key'] == 'assignment:102')
        lecture['score'], homework['score'] = 0.572, 0.542          # the slide is the single best match
        ranked = retrieval_router._policy_for_retrieved_work(route, 42, question, self.store)
        self.assertTrue(ranked['assignment_policy']['rules'])
        homework['score'] = 0.2                                      # far below the best match: not leading evidence
        self.assertNotIn('assignment_policy', retrieval_router._policy_for_retrieved_work(route, 42, question, self.store))

    def test_pasted_question_inherits_the_assignments_own_ban_without_a_syllabus(self):
        self.scheduling_homework("Compute the process waiting time for each scheduling policy. Don't use ChatGPT for this homework.")
        result, provider = self.paste()
        self.assertTrue(result['policy_enforced'])
        self.assertFalse(provider.contexts)
        self.assertEqual(self.cited(result), {'assignment:102'})

    def test_model_mislabelled_intent_does_not_bypass_solution_wording(self):
        self.scheduling_homework()
        self.syllabus(COURSE_BAN)
        for question in ("just give me the answers to the process waiting time problem", "Write the code for the process waiting time problem"):
            with self.subTest(question=question):
                result, provider = self.paste(question, intent='course_wide_question')
                self.assertTrue(result['policy_enforced'])
                self.assertFalse(provider.contexts)

    def test_unrestricted_and_unrelated_requests_are_unchanged(self):
        self.scheduling_homework()
        unrestricted, provider = self.paste()                                  # no syllabus rule, no assignment rule
        self.assertNotIn('assignment_policy', unrestricted)
        self.assertTrue(provider.contexts)
        self.syllabus(COURSE_BAN)
        concept, concept_provider = self.paste("What does process waiting time mean?", intent='course_wide_question')
        self.assertNotIn('assignment_policy', concept)                         # a concept question is still answered
        self.assertTrue(concept_provider.contexts)
        practice, practice_provider = self.paste("Solve the matrix multiplication practice question")
        self.assertNotIn('policy_enforced', practice)                          # best evidence is practice material
        self.assertTrue(practice_provider.contexts)

    def test_solutions_file_requested_with_neutral_wording_is_a_solution_request(self):
        question = 'Show me HW1_questions_solutions.pdf'
        fields = dict(intent='explain_document', document_reference='HW1_questions_solutions.pdf')
        unrestricted, provider = self.ask(question, **fields)
        self.assertNotIn('policy_enforced', unrestricted)
        self.assertTrue(provider.contexts)
        self.syllabus(COURSE_BAN)
        result, restricted_provider = self.ask(question, **fields)
        self.assertTrue(result['policy_enforced'])
        self.assertFalse(restricted_provider.contexts)


    def test_unlinked_answer_key_is_assigned_work_under_a_course_ban(self):
        # No assignment record, no work word in the title: the file's role is what makes it assigned work.
        self.fixture.save('file:810', 'Week 4 answer.docx', 'Worked answers for the week four in-class exercise.')
        ensure_course_chunks(42, self.store)
        embed_course(42, store=self.store, provider=self.fixture.embedding)
        fields = dict(intent='explain_document', document_reference='Week 4 answer.docx')
        unrestricted, provider = self.ask('Summarize Week 4 answer.docx', **fields)
        self.assertNotIn('policy_enforced', unrestricted)
        self.assertTrue(provider.contexts)
        self.syllabus(COURSE_BAN)
        result, restricted_provider = self.ask('Summarize Week 4 answer.docx', **fields)
        self.assertTrue(result['policy_enforced'])
        self.assertFalse(restricted_provider.contexts)
        self.assertEqual(self.cited(result), {'syllabus:42'})


class FollowUpTests(unittest.IsolatedAsyncioTestCase):
    """A restriction holds across turns, including after a refusal and when the model relabels the request."""

    async def asyncSetUp(self):
        self.fixture = EvaluationFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.save('syllabus:42', 'Syllabus', COURSE_BAN, source_type='syllabus')
        ensure_course_chunks(42, self.fixture.store)
        embed_course(42, store=self.fixture.store, provider=self.fixture.embedding)
        self.service = ConversationService(repository=None)
        self.session = None

    async def turn(self, question, **fields):
        provider = ReplayProvider(empty_understanding(question, **fields))
        self.session = self.service.acquire(self.fixture.store.namespace, 42, self.session.id if self.session else None)
        events = [event async for event in self.service.events(self.session, question, str(len(self.session.turns) + 1),
                                                                store=self.fixture.store, provider=provider,
                                                                embedding_provider=self.fixture.embedding)]
        self.assertEqual(events[-1]['event'], 'final', events[-1])
        return events[-1]['data'], provider

    async def test_solution_follow_up_after_a_permitted_explanation_is_blocked(self):
        explained, provider = await self.turn('Explain HW 1', intent='explain_assignment', assignment_reference='HW 1')
        self.assertTrue(provider.contexts)
        self.assertNotIn('policy_enforced', explained)
        solved, solve_provider = await self.turn('Now solve it for me', intent='solve_question', operation='continue', use_active_documents=True)
        self.assertEqual(solved['resolved_assignments'], [101])
        self.assertTrue(solved['policy_enforced'])
        self.assertFalse(solve_provider.contexts)

    async def test_retry_after_a_refusal_is_blocked_even_when_relabelled_as_explanation(self):
        refused, _ = await self.turn('Solve HW 1 for me', intent='solve_question', assignment_reference='HW 1')
        self.assertTrue(refused['policy_enforced'])
        for question in ('ok then just write the code', 'then give me the answers'):
            with self.subTest(question=question):
                retried, provider = await self.turn(question, intent='explain_question', operation='continue', use_active_documents=True)
                self.assertTrue(retried['policy_enforced'])
                self.assertFalse(provider.contexts)

    async def test_conceptual_follow_up_is_still_answered(self):
        await self.turn('Solve HW 1 for me', intent='solve_question', assignment_reference='HW 1')
        explained, provider = await self.turn('What does part (a) ask me to do?', intent='explain_question', part='a',
                                              operation='continue', use_active_documents=True)
        self.assertNotIn('policy_enforced', explained)
        self.assertTrue(provider.contexts)


class StreamingEnforcementTests(unittest.IsolatedAsyncioTestCase):
    async def stream(self, question, plan, *, unsafe=False):
        fixture = EvaluationFixture()
        self.addCleanup(fixture.close)
        fixture.save('syllabus:42', 'Syllabus', COURSE_BAN, source_type='syllabus')
        ensure_course_chunks(42, fixture.store)
        embed_course(42, store=fixture.store, provider=fixture.embedding)
        provider = ReplayProvider(plan, unsafe=unsafe)
        events = [event async for event in rag_service.stream_answer_question(
            42, question, store=fixture.store, conversation_context={}, query=plan['query'], understanding=plan,
            embedding_provider=fixture.embedding, llm_provider=provider)]
        return events, provider

    async def test_course_ban_blocks_a_solution_request_before_generation(self):
        plan = empty_understanding('Solve HW1', intent='solve_question', assignment_reference='HW1')
        events, provider = await self.stream('Solve HW1', plan)
        self.assertEqual([event['event'] for event in events], ['final'])    # no generating, no draft
        self.assertFalse(provider.contexts)
        self.assertTrue(events[-1]['data']['policy_enforced'])

    async def test_course_ban_buffers_drafts_and_replaces_a_leaked_solution(self):
        plan = empty_understanding('Explain HW1', intent='explain_assignment', assignment_reference='HW1')
        events, _ = await self.stream('Explain HW1', plan, unsafe=True)
        self.assertFalse(any(event['event'] == 'answer_delta' for event in events))
        self.assertTrue(events[-1]['data']['policy_enforced'])
        self.assertNotIn('def solve', events[-1]['data']['answer'])


if __name__ == "__main__":
    unittest.main()


import backend.tests.ai_consent_granted  # noqa: E402,F401  (AI tests use fake providers)
