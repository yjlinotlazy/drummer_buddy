import { RefObject, useEffect, useRef, useState } from "react";
import { Beam, Formatter, Renderer, Stave, StaveNote, Voice } from "vexflow";

type ScoreEvent = {
  id: string;
  instrument: string;
  detectedTimeSec: number;
  onsetTick: number;
};

type Score = {
  ppq: number;
  beats: { timeSec: number; measure: number; beat: number }[];
  timeSignatures: { measure: number; numerator: number; denominator: number }[];
  events: ScoreEvent[];
};

const drumKey: Record<string, string> = {
  kick: "f/4",
  snare: "c/5",
  tom_low: "a/4",
  tom_mid: "b/4",
  tom_high: "d/5",
  hihat_closed: "g/5/x2",
  hihat_open: "g/5/x3",
  crash: "a/5/x2",
  ride: "f/5/x2",
};

export default function ScoreView({ songId, audioRef }: { songId: string; audioRef: RefObject<HTMLAudioElement | null> }) {
  const host = useRef<HTMLDivElement>(null);
  const [score, setScore] = useState<Score | null>(null);
  const [error, setError] = useState("");
  const [width, setWidth] = useState(760);

  useEffect(() => {
    fetch(`/api/songs/${songId}/score`)
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("Could not load score")))
      .then(setScore)
      .catch((caught) => setError(caught instanceof Error ? caught.message : "Could not load score"));
  }, [songId]);

  useEffect(() => {
    if (!host.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(host.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const element = host.current;
    if (!element || !score || width < 200) return;
    element.replaceChildren();
    const timeSignature = score.timeSignatures[0] ?? { numerator: 4, denominator: 4 };
    const measureTicks = timeSignature.numerator * score.ppq * 4 / timeSignature.denominator;
    const eventMeasures = score.events.length ? Math.floor(Math.max(...score.events.map((event) => event.onsetTick)) / measureTicks) + 1 : 1;
    const beatMeasures = Math.max(1, ...score.beats.map((beat) => beat.measure));
    const measureCount = Math.max(eventMeasures, beatMeasures);
    const measuresPerRow = width < 560 ? 1 : width < 820 ? 2 : 4;
    const rowCount = Math.ceil(measureCount / measuresPerRow);
    const staveWidth = Math.floor((width - 20) / measuresPerRow);
    const renderer = new Renderer(element, Renderer.Backends.SVG);
    renderer.resize(width, rowCount * 130 + 20);
    const context = renderer.getContext();

    for (let measureIndex = 0; measureIndex < measureCount; measureIndex += 1) {
      const x = 10 + (measureIndex % measuresPerRow) * staveWidth;
      const y = 18 + Math.floor(measureIndex / measuresPerRow) * 130;
      const stave = new Stave(x, y, staveWidth);
      if (measureIndex % measuresPerRow === 0) stave.addClef("percussion");
      if (measureIndex === 0) stave.addTimeSignature(`${timeSignature.numerator}/${timeSignature.denominator}`);
      stave.setContext(context).draw();

      const slotTicks = score.ppq / 4;
      const slots = timeSignature.numerator * 4;
      const startTick = measureIndex * measureTicks;
      const notes: StaveNote[] = [];
      const eventsBySlot: ScoreEvent[][] = [];
      for (let slot = 0; slot < slots; slot += 1) {
        const slotStart = startTick + slot * slotTicks;
        const slotEvents = score.events.filter((event) => Math.abs(event.onsetTick - slotStart) < slotTicks / 2);
        eventsBySlot.push(slotEvents);
        const keys = [...new Set(slotEvents.map((event) => drumKey[event.instrument]).filter(Boolean))];
        notes.push(new StaveNote({
          clef: "percussion",
          keys: keys.length ? keys : ["b/4"],
          duration: keys.length ? "16" : "16r",
        }));
      }
      const voice = new Voice({ numBeats: timeSignature.numerator, beatValue: timeSignature.denominator });
      voice.addTickables(notes);
      new Formatter().joinVoices([voice]).format([voice], staveWidth - (measureIndex % measuresPerRow === 0 ? 80 : 28));
      voice.draw(context, stave);
      Beam.generateBeams(notes).forEach((beam) => beam.setContext(context).draw());
      notes.forEach((note, slot) => {
        const svg = note.getSVGElement();
        const slotEvents = eventsBySlot[slot];
        if (svg && slotEvents.length) {
          svg.dataset.eventIds = slotEvents.map((event) => event.id).join(",");
          svg.dataset.time = String(Math.min(...slotEvents.map((event) => event.detectedTimeSec)));
          svg.classList.add("score-event");
        }
      });
    }
  }, [score, width]);

  useEffect(() => {
    let frame = 0;
    let active: Element | null = null;
    const update = () => {
      const audio = audioRef.current;
      const nodes = host.current?.querySelectorAll<SVGGElement>(".score-event");
      if (audio && nodes?.length) {
        let next: Element | null = null;
        let nextTime = -Infinity;
        for (const node of nodes) {
          const time = Number(node.dataset.time);
          if (time <= audio.currentTime + 0.03 && time > nextTime) {
            next = node;
            nextTime = time;
          }
        }
        if (next !== active) {
          active?.classList.remove("active");
          next?.classList.add("active");
          active = next;
        }
      }
      frame = requestAnimationFrame(update);
    };
    frame = requestAnimationFrame(update);
    return () => cancelAnimationFrame(frame);
  }, [audioRef, score]);

  if (error) return <p className="error">{error}</p>;
  if (!score) return <p className="empty">Loading score…</p>;
  return <div className="score-view" ref={host} />;
}
