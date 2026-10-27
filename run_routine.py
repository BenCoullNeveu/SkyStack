"""SkyStack pipeline runner.

    python run_routine.py                       # transfer + master calibration frames (as before)
    python run_routine.py run    -t "M 31" [-s 2024-12-05 ...] [--triage local|send|build]
        [transfer] -> [masters] -> calibrate -> measure -> reference -> register -> triage
        --triage local  opens the triage tool here; "Finish review" carries on with
                        apply decisions -> stack -> [postprocess] -> [export]
        --triage send   (default) sends the page to reviewers and stops
    python run_routine.py finish -t "M 31" decisions.json [more.json ...]
                                                # apply triage -> stack -> [postprocess] -> [export]

Each stage can also be run on its own:
    transfer | masters | calibrate | measure | reference | register | triage [--local] |
    apply-triage | stack | postprocess | export | add-processed | status

-t defaults to config.yaml's target_name. -s/--session limits the run to
session folders under LIGHT/<target>/ (repeatable); default is every session.
"""
import argparse
import json
import logging
import os
from pathlib import Path

from astro_utils.config_loader import load_config
from skystack import pipeline as P

config = load_config(Path(__file__).parent / "config.yaml")
LOG_LEVEL = str(config.get("log_level")).upper()
logging.basicConfig(level=LOG_LEVEL,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

TRIAGE_MODES = ("local", "web", "send", "build")


def build_parser():
    parser = argparse.ArgumentParser(description="SkyStack processing pipeline")
    sub = parser.add_subparsers(dest="command")

    def add(name, help_, target=True, sessions=True):
        p = sub.add_parser(name, help=help_)
        if target:
            p.add_argument("-t", "--target", default=config.get("target_name"),
                           help="Target folder name under LIGHT/ (default: config target_name)")
        if sessions:
            p.add_argument("-s", "--session", action="append", dest="sessions", metavar="FOLDER",
                           help="Session folder under LIGHT/<target>/ (repeatable; default: all)")
        return p

    def add_reference_args(p):
        p.add_argument("--reference-frame", metavar="FILE",
                       help="Register against this light (path or file name) instead of the "
                            "plate-solved bounding-box canvas")
        p.add_argument("--canvas", choices=["union", "intersection"],
                       help="Canvas mode (default: registration.canvas in config)")

    def add_export_args(p):
        p.add_argument("--raw-lights", choices=["none", "kept", "all"],
                       help="Include raw LIGHT frames (default: client_export.include_raw_lights)")
        p.add_argument("--with-calibration", action="store_true", default=None,
                       help="Include the master flats/darks used for the included raw lights")
        p.add_argument("--no-calibration", action="store_false", dest="with_calibration",
                       help="Leave calibration masters out even if config includes them")
        p.add_argument("--processed", action=argparse.BooleanOptionalAction, default=None,
                       help="Include the post-processed (e.g. BlurXTerminator-corrected) masters "
                            "(default: client_export.include_processed)")

    def add_after_triage_args(p):
        """What happens once triage decisions are in: stack, then these."""
        p.add_argument("--postprocess", action=argparse.BooleanOptionalAction, default=None,
                       help="Post-process the masters after stacking (default: postprocess.after_stack)")
        p.add_argument("--step", action="append", dest="steps", metavar="PRESET",
                       help="Post-processing preset, in order (repeatable; default: postprocess.default_steps)")
        p.add_argument("--export", action=argparse.BooleanOptionalAction, default=None,
                       help="Write the client zip (default: only for client_export.client_targets)")
        add_export_args(p)

    run = add("run", "calibrate -> measure -> reference -> register -> triage (and on, with --triage local)")
    run.add_argument("--skip-transfer", action="store_true")
    run.add_argument("--skip-masters", action="store_true")
    run.add_argument("--skip-measure", action="store_true", help="Don't measure frame metrics")
    run.add_argument("--triage", choices=TRIAGE_MODES, default=None,
                     help="local: review in a window on this PC, then carry on; web: review in "
                          "your own browser (dashboard link), then carry on; send: to reviewers "
                          "(default: remote_triage.mode, else send); build: make the page only")
    run.add_argument("--no-deliver", action="store_true", help="Same as --triage build")
    add_reference_args(run)
    add_after_triage_args(run)

    fin = add("finish", "Apply triage decisions, stack, then [postprocess] and [export]")
    fin.add_argument("decisions", nargs="+", type=Path, help="Decisions JSON file(s) from reviewers")
    fin.add_argument("--manifest", type=Path, help="Triage manifest (default: the last package sent)")
    add_after_triage_args(fin)

    add("transfer", "Copy new files from Dropbox to local storage", target=False, sessions=False)
    add("masters", "Build master flats/darks", target=False, sessions=False)
    add("calibrate", "Calibrate the target's lights")
    mea = add("measure", "Measure FWHM, eccentricity, stars, PSF signal weight, ... (SubframeSelector)")
    mea.add_argument("--force", action="store_true", help="Re-measure frames already measured")
    add_reference_args(add("reference", "Plate solve and build the registration reference"))
    reg = add("register", "Register calibrated lights to the reference")
    reg.add_argument("--force", action="store_true", help="Re-register frames already registered")
    tri = add("triage", "Triage the registered frames: send the page to reviewers, or --local to review here")
    tri.add_argument("--local", action="store_true",
                     help="Open the triage tool here; Finish review applies the decisions and carries on")
    tri.add_argument("--web", action="store_true",
                     help="Make the triage tool available in your own browser (link in the dashboard); "
                          "Finish review there queues the rest")
    tri.add_argument("--no-continue", action="store_true",
                     help="With --local: only apply the decisions, don't stack afterwards")
    tri.add_argument("--no-deliver", action="store_true", help="Build the page but don't send it")
    tri.add_argument("--all", action="store_true",
                     help="Every registered frame, including ones already kept/rejected (to redo a triage); "
                          "default: only frames waiting for triage")
    add_after_triage_args(tri)
    app = add("apply-triage", "Move frames according to returned decisions", sessions=False)
    app.add_argument("decisions", nargs="+", type=Path)
    app.add_argument("--manifest", type=Path)
    app.add_argument("--dry-run", action="store_true")
    add("stack", "Local normalization + integration of triaged frames", sessions=False)
    post = add("postprocess", "Apply post-processing presets (e.g. BlurXTerminator) to the masters",
               sessions=False)
    post.add_argument("--step", action="append", dest="steps", metavar="PRESET",
                      help="Preset name, in order (repeatable; default: postprocess.default_steps)")
    post.add_argument("--master", action="append", dest="masters", metavar="FILE",
                      help="masterLight file name in master/ (repeatable; default: all)")
    add_export_args(add("export", "Write the client zip"))
    addp = add("add-processed", "Add the post-processed masters to the existing client zip", sessions=False)
    addp.add_argument("--zip", type=Path, help="Client zip to update (default: the target's last export)")
    add("status", "Show what each stage last did for the target", sessions=False)
    return parser


def require_target(args):
    if not getattr(args, "target", None):
        raise SystemExit("No target given: pass -t/--target or set target_name in config.yaml")
    return args.target


def _after_triage_plan(args, target):
    """(postprocess?, export?) once decisions are applied and frames stacked."""
    do_post = args.postprocess if args.postprocess is not None else P.is_postprocess_after_stack(config)
    do_export = args.export if args.export is not None else P.is_client_target(config, target)
    return do_post, do_export


def _next_steps_text(args, target):
    do_post, do_export = _after_triage_plan(args, target)
    steps = ["stacking"]
    if do_post:
        steps.append("post-processing (" + ", ".join(args.steps or P.postprocess_default_steps(config)) + ")")
    if do_export:
        steps.append("client export")
    return " → ".join(steps)


def finish_after_triage(args, target, decisions, manifest=None, sessions=None):
    """apply decisions -> stack -> [postprocess] -> [export]"""
    P.stage_apply_triage(config, target, decisions, manifest)
    P.stage_stack(config, target)
    do_post, do_export = _after_triage_plan(args, target)
    if do_post:
        P.stage_postprocess(config, target, args.steps)
    if do_export:
        out = P.stage_export(config, target, args.raw_lights, args.with_calibration, sessions, args.processed)
        logger.info("Client export: %s", out)


def _then_argv(args, target, sessions, carry_on=True):
    """The job to queue once a web review is finished (its decisions file is appended)."""
    from skystack import jobs

    if not carry_on:
        return ["apply-triage", "-t", target]
    argv = jobs.pipeline_argv("finish", target, sessions or (), decisions=["<decisions>"],
                              postprocess=args.postprocess, steps=args.steps or (), export=args.export,
                              raw_lights=args.raw_lights, with_calibration=args.with_calibration,
                              processed=args.processed)
    return argv[:-1]


def triage_on_web(args, target, sessions, carry_on=True, scope="pending"):
    next_steps = _next_steps_text(args, target) if carry_on else "applying the decisions"
    from skystack.jobs import REQUESTED_BY_ENV
    from skystack.triage_server import session_path

    entry = P.stage_triage_web(config, target, sessions, then_argv=_then_argv(args, target, sessions, carry_on),
                               next_steps=next_steps, scope=scope,
                               requested_by=os.environ.get(REQUESTED_BY_ENV))
    logger.info("Open the triage tool from the dashboard (Triage tab), or at %s on the dashboard's address.",
                session_path(entry))


def triage_here(args, target, sessions, carry_on=True, scope="pending"):
    next_steps = _next_steps_text(args, target) if carry_on else "applying the decisions"
    decisions = P.stage_triage_local(config, target, sessions, next_steps=next_steps, scope=scope)
    if carry_on:
        finish_after_triage(args, target, [decisions], sessions=sessions)
    else:
        P.stage_apply_triage(config, target, [decisions])


def send_triage(target, sessions, deliver, scope="pending"):
    info = P.stage_triage_send(config, target, sessions, deliver=deliver, scope=scope)
    logger.info("Triage package ready: %s", info["package"])
    logger.info('When decisions come back: python run_routine.py finish -t "%s" <decisions.json>', target)


def main():
    args = build_parser().parse_args()
    cmd = args.command
    sessions = getattr(args, "sessions", None)

    if cmd is None:
        P.stage_transfer(config)
        P.stage_masters(config)
        return
    if cmd == "transfer":
        return P.stage_transfer(config)
    if cmd == "masters":
        return P.stage_masters(config)

    target = require_target(args)

    if cmd == "run":
        mode = "build" if args.no_deliver else (args.triage or P.default_triage_mode(config))
        if not args.skip_transfer:
            P.stage_transfer(config)
        if not args.skip_masters:
            P.stage_masters(config)
        P.stage_calibrate(config, target, sessions)
        if not args.skip_measure:
            P.stage_measure(config, target, sessions)
        P.stage_reference(config, target, sessions, args.reference_frame, args.canvas)
        P.stage_register(config, target, sessions)
        if mode == "local":
            triage_here(args, target, sessions)
        elif mode == "web":
            triage_on_web(args, target, sessions)
        else:
            send_triage(target, sessions, deliver=mode == "send")
    elif cmd == "finish":
        finish_after_triage(args, target, args.decisions, args.manifest, sessions)
    elif cmd == "calibrate":
        P.stage_calibrate(config, target, sessions)
    elif cmd == "measure":
        info = P.stage_measure(config, target, sessions, force=args.force)
        logger.info("Measured %d frames (%d failed)", info["measured"], len(info["failed"]))
    elif cmd == "reference":
        logger.info("Reference: %s", P.stage_reference(config, target, sessions, args.reference_frame, args.canvas))
    elif cmd == "register":
        P.stage_register(config, target, sessions, force=args.force)
    elif cmd == "triage":
        scope = "all" if args.all else "pending"
        if args.local:
            triage_here(args, target, sessions, carry_on=not args.no_continue, scope=scope)
        elif args.web:
            triage_on_web(args, target, sessions, carry_on=not args.no_continue, scope=scope)
        else:
            send_triage(target, sessions, deliver=not args.no_deliver, scope=scope)
    elif cmd == "apply-triage":
        summary = P.stage_apply_triage(config, target, args.decisions, args.manifest, dry_run=args.dry_run)
        print(json.dumps({k: v for k, v in summary.items() if k not in ("moved", "deleted")}, indent=2))
    elif cmd == "stack":
        P.stage_stack(config, target)
    elif cmd == "postprocess":
        results = P.stage_postprocess(config, target, args.steps, args.masters)
        failed = [r for r in results if r.get("status") != "ok"]
        if failed:
            raise SystemExit(f"{len(failed)} of {len(results)} masters failed post-processing")
    elif cmd == "add-processed":
        info = P.stage_add_processed(config, target, args.zip)
        logger.info("Added %d post-processed masters to %s", len(info["added"]), info["zip"])
    elif cmd == "export":
        out = P.stage_export(config, target, args.raw_lights, args.with_calibration, sessions, args.processed)
        logger.info("Client export: %s", out)
    elif cmd == "status":
        print(json.dumps(P.load_state(P.TargetPaths.from_config(config, target)), indent=2))


if __name__ == "__main__":
    main()
