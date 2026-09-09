"""Regression tests for mandatory human promotion boundary."""

import pytest
import sys
from unittest.mock import MagicMock, patch
from fusion_agent.cli.main import main, cmd_run
from fusion_agent.models.deliberation import ReviewStatus


def test_cli_parser_rejects_yes_flag():
    """Verify that --yes / -y flag has been completely removed from CLI parser."""
    test_args_1 = ["fusion", "run", "dummy task", "--yes"]
    with patch.object(sys, "argv", test_args_1):
        with pytest.raises(SystemExit):
            main()

    test_args_2 = ["fusion", "run", "dummy task", "-y"]
    with patch.object(sys, "argv", test_args_2):
        with pytest.raises(SystemExit):
            main()


def test_promotion_interactive_blank_input_rejects(tmp_path):
    """Verify that pressing Enter (blank input) defaults to N and rejects promotion."""
    args = MagicMock()
    args.dir = str(tmp_path)
    args.task = "test task"
    args.no_promote = False
    args.debug = False

    session = MagicMock()
    session.task_branch = "fusion/task-test"
    session.base_commit = "abcd1234"
    session.base_branch = "master"

    verif = MagicMock(passed=True, exit_code=0, duration_seconds=1.0, stderr="")
    review = MagicMock(status=ReviewStatus.APPROVED, reviewer_agent="Reviewer", comments="Approved")
    diff = "diff --git a/f b/f\n+new line"

    mock_result = MagicMock()
    mock_result.workspace_session = session
    mock_result.verification_result = verif
    mock_result.review_result = review
    mock_result.diff = diff
    mock_result.final_answer = "Done"
    mock_result.deliberation.strategy_used = "AUTONOMOUS_EDIT"
    mock_result.deliberation.participating_providers = ["Mock"]
    mock_result.deliberation.rounds_executed = 1
    mock_result.deliberation.duration_ms = 100.0
    mock_result.deliberation.total_input_tokens = 100
    mock_result.deliberation.total_output_tokens = 100
    mock_result.deliberation.stage_metrics = []
    mock_result.deliberation.proposals = []
    mock_result.deliberation.critiques = []
    mock_result.deliberation.reviews = []

    with patch("fusion_agent.cli.main.ConfigLoader.find_config_file", return_value="dummy_cfg"), \
         patch("fusion_agent.cli.main.ConfigLoader.load"), \
         patch("fusion_agent.cli.main.Database"), \
         patch("fusion_agent.cli.main.FusionOrchestrator.run_task", return_value=mock_result), \
         patch("fusion_agent.cli.main.PromotionEngine") as MockPromoEngine, \
         patch("builtins.input", return_value=""):  # User presses Enter
        
        promo_instance = MockPromoEngine.return_value
        cmd_run(args)

        # Blank input must discard, not promote!
        promo_instance.discard.assert_called_once_with(session)
        promo_instance.promote.assert_not_called()


def test_promotion_interactive_n_input_rejects(tmp_path):
    """Verify that 'n' or 'N' explicitly rejects promotion."""
    args = MagicMock()
    args.dir = str(tmp_path)
    args.task = "test task"
    args.no_promote = False
    args.debug = False

    session = MagicMock()
    session.task_branch = "fusion/task-test"
    session.base_commit = "abcd1234"
    session.base_branch = "master"

    verif = MagicMock(passed=True, exit_code=0, duration_seconds=1.0, stderr="")
    review = MagicMock(status=ReviewStatus.APPROVED, reviewer_agent="Reviewer", comments="Approved")
    diff = "diff --git a/f b/f\n+new line"

    mock_result = MagicMock()
    mock_result.workspace_session = session
    mock_result.verification_result = verif
    mock_result.review_result = review
    mock_result.diff = diff
    mock_result.final_answer = "Done"
    mock_result.deliberation.strategy_used = "AUTONOMOUS_EDIT"
    mock_result.deliberation.stage_metrics = []
    mock_result.deliberation.proposals = []
    mock_result.deliberation.critiques = []
    mock_result.deliberation.reviews = []

    with patch("fusion_agent.cli.main.ConfigLoader.find_config_file", return_value="dummy_cfg"), \
         patch("fusion_agent.cli.main.ConfigLoader.load"), \
         patch("fusion_agent.cli.main.Database"), \
         patch("fusion_agent.cli.main.FusionOrchestrator.run_task", return_value=mock_result), \
         patch("fusion_agent.cli.main.PromotionEngine") as MockPromoEngine, \
         patch("builtins.input", return_value="N"):
        
        promo_instance = MockPromoEngine.return_value
        cmd_run(args)

        promo_instance.discard.assert_called_once_with(session)
        promo_instance.promote.assert_not_called()


def test_promotion_interactive_explicit_y_promotes(tmp_path):
    """Verify that explicit interactive 'y' is required to promote."""
    args = MagicMock()
    args.dir = str(tmp_path)
    args.task = "test task"
    args.no_promote = False
    args.debug = False

    session = MagicMock()
    session.task_branch = "fusion/task-test"
    session.base_commit = "abcd1234"
    session.base_branch = "master"

    verif = MagicMock(passed=True, exit_code=0, duration_seconds=1.0, stderr="")
    review = MagicMock(status=ReviewStatus.APPROVED, reviewer_agent="Reviewer", comments="Approved")
    diff = "diff --git a/f b/f\n+new line"

    mock_result = MagicMock()
    mock_result.workspace_session = session
    mock_result.verification_result = verif
    mock_result.review_result = review
    mock_result.diff = diff
    mock_result.final_answer = "Done"
    mock_result.deliberation.strategy_used = "AUTONOMOUS_EDIT"
    mock_result.deliberation.stage_metrics = []
    mock_result.deliberation.proposals = []
    mock_result.deliberation.critiques = []
    mock_result.deliberation.reviews = []

    with patch("fusion_agent.cli.main.ConfigLoader.find_config_file", return_value="dummy_cfg"), \
         patch("fusion_agent.cli.main.ConfigLoader.load"), \
         patch("fusion_agent.cli.main.Database"), \
         patch("fusion_agent.cli.main.FusionOrchestrator.run_task", return_value=mock_result), \
         patch("fusion_agent.cli.main.PromotionEngine") as MockPromoEngine, \
         patch("builtins.input", return_value="y"):
        
        promo_instance = MockPromoEngine.return_value
        promo_instance.promote.return_value = MagicMock(success=True, message="Promoted")
        cmd_run(args)

        promo_instance.promote.assert_called_once_with(session)
        promo_instance.discard.assert_not_called()


def test_promotion_no_promote_flag_bypasses_input_and_discards(tmp_path):
    """Verify that --no-promote flag cleanly discards without prompting input."""
    args = MagicMock()
    args.dir = str(tmp_path)
    args.task = "test task"
    args.no_promote = True
    args.debug = False

    session = MagicMock()
    session.task_branch = "fusion/task-test"
    session.base_commit = "abcd1234"
    session.base_branch = "master"

    verif = MagicMock(passed=True, exit_code=0, duration_seconds=1.0, stderr="")
    review = MagicMock(status=ReviewStatus.APPROVED, reviewer_agent="Reviewer", comments="Approved")
    diff = "diff --git a/f b/f\n+new line"

    mock_result = MagicMock()
    mock_result.workspace_session = session
    mock_result.verification_result = verif
    mock_result.review_result = review
    mock_result.diff = diff
    mock_result.final_answer = "Done"
    mock_result.deliberation.strategy_used = "AUTONOMOUS_EDIT"
    mock_result.deliberation.stage_metrics = []
    mock_result.deliberation.proposals = []
    mock_result.deliberation.critiques = []
    mock_result.deliberation.reviews = []

    with patch("fusion_agent.cli.main.ConfigLoader.find_config_file", return_value="dummy_cfg"), \
         patch("fusion_agent.cli.main.ConfigLoader.load"), \
         patch("fusion_agent.cli.main.Database"), \
         patch("fusion_agent.cli.main.FusionOrchestrator.run_task", return_value=mock_result), \
         patch("fusion_agent.cli.main.PromotionEngine") as MockPromoEngine, \
         patch("builtins.input") as mock_input:
        
        promo_instance = MockPromoEngine.return_value
        cmd_run(args)

        mock_input.assert_not_called()
        promo_instance.discard.assert_called_once_with(session)
        promo_instance.promote.assert_not_called()
