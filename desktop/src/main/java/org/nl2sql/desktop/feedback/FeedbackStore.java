package org.nl2sql.desktop.feedback;

import org.nl2sql.desktop.api.Json;
import org.nl2sql.desktop.api.Models;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.Executor;
import java.util.function.Consumer;
import java.util.function.Supplier;

/**
 * Was that answer right? Yes or no, and what became of the answer.
 *
 * <p>The design is the web interface's, because the behaviour should be: the
 * request is <em>not</em> awaited before the button changes state. A verdict
 * is a courtesy the user is doing us, and making them watch a spinner for it
 * is how a feedback system stops collecting feedback. So the record is written
 * first and is what the interface reads, the send goes out behind it, and the
 * record's {@link SyncState} carries what happened -- which is why that field
 * exists rather than the failure being swallowed or thrown at a user who has
 * already moved on.
 *
 * <p>The file underneath is not a cache. A verdict given while the server was
 * unreachable is still on screen after a restart, still marked as unsent, and
 * still the user's own record of what they thought.
 *
 * <p><b>Threads.</b> Votes arrive on the JavaFX thread and sends complete on
 * another, so every method that touches the records is synchronized.
 * Listeners are called on whichever thread caused the change, which means a
 * listener that draws has to hop to the FX thread itself. That is the right
 * way round: this class knows nothing about JavaFX, and the two callers that
 * do are the two that should say so.
 */
public final class FeedbackStore {

    /** Enough to be useful, small enough never to be a problem. */
    public static final int MAX_RECORDS = 200;

    /** The file's shape, versioned so a later one can be recognised. */
    record Saved(int version, List<FeedbackRecord> records) {
    }

    private static final int FORMAT_VERSION = 1;

    private final Sender sender;
    private final Path file;
    private final Executor executor;
    private final Supplier<Instant> clock;
    private final Consumer<Exception> onError;

    private final List<Runnable> listeners = new CopyOnWriteArrayList<>();
    /** The comment last sent for a job, so a retry resends it too. */
    private final Map<String, String> comments = new LinkedHashMap<>();
    private List<FeedbackRecord> records;

    /**
     * @param file     where verdicts are mirrored, or null to keep them for
     *                 this session only
     * @param executor what the sends run on. The tests pass
     *                 {@code Runnable::run} and get the whole path, in order,
     *                 on one thread
     * @param onError  told about every send that failed, for a status bar
     */
    public FeedbackStore(Sender sender, Path file, Executor executor, Supplier<Instant> clock,
                         Consumer<Exception> onError) {
        this.sender = sender;
        this.file = file;
        this.executor = executor;
        this.clock = clock;
        this.onError = onError;
        this.records = read();
    }

    /** True when a verdict given here goes anywhere. */
    public boolean sends() {
        return sender.enabled();
    }

    public synchronized Optional<FeedbackRecord> get(String jobId) {
        return records.stream().filter(record -> record.jobId().equals(jobId)).findFirst();
    }

    /** Newest first. */
    public synchronized List<FeedbackRecord> all() {
        return List.copyOf(records);
    }

    /** Listen for changes. The returned runnable removes the listener again. */
    public Runnable subscribe(Runnable listener) {
        listeners.add(listener);
        return () -> listeners.remove(listener);
    }

    /** Record a verdict, or replace one, and send it. */
    public void vote(String jobId, String question, Models.Verdict verdict) {
        send(jobId, question, verdict, "");
    }

    /**
     * Record a verdict and a comment together.
     *
     * <p>Separate from {@link #vote} because the comment arrives later than
     * the verdict: the user clicks Yes or No first and may then explain. Both
     * write the same row, so the second is an amendment rather than a second
     * opinion.
     */
    public void comment(String jobId, String question, Models.Verdict verdict, String comment) {
        send(jobId, question, verdict, comment);
    }

    /** Withdraw a verdict, for the misclick. */
    public void withdraw(String jobId) {
        boolean had;
        synchronized (this) {
            had = get(jobId).isPresent();
            records = records.stream().filter(record -> !record.jobId().equals(jobId)).toList();
            comments.remove(jobId);
        }
        announce();
        if (!had || !sender.enabled()) {
            return;
        }
        executor.execute(() -> {
            try {
                sender.withdraw(jobId);
            } catch (RuntimeException cause) {
                // A withdrawal that does not reach the server leaves a row
                // staged that the user believes they took back. It is
                // reported rather than ignored -- but the local record stays
                // gone, because re-showing a verdict somebody just withdrew
                // would be the more confusing lie.
                onError.accept(cause);
            }
        });
    }

    /** Send a verdict whose last send failed. */
    public void retry(String jobId) {
        FeedbackRecord record;
        String comment;
        synchronized (this) {
            record = get(jobId).orElse(null);
            comment = comments.getOrDefault(jobId, "");
        }
        if (record == null || record.sync() != SyncState.FAILED) {
            return;
        }
        send(jobId, record.question(), record.verdict(), comment);
    }

    private void send(String jobId, String question, Models.Verdict verdict, String comment) {
        if (!sender.enabled()) {
            put(new FeedbackRecord(jobId, question, verdict, now(), SyncState.LOCAL, ""));
            return;
        }
        put(new FeedbackRecord(jobId, question, verdict, now(), SyncState.SAVING, ""));
        synchronized (this) {
            comments.put(jobId, comment);
        }
        executor.execute(() -> {
            try {
                sender.submit(jobId, verdict, comment);
                settle(jobId, verdict, SyncState.SAVED, "");
            } catch (RuntimeException cause) {
                settle(jobId, verdict, SyncState.FAILED, message(cause));
                onError.accept(cause);
            }
        });
    }

    /**
     * Move a record to its final state, if it is still the one that was sent.
     *
     * <p>A user who changed their mind while the request was in flight has a
     * newer verdict on screen, and overwriting it with the older one's outcome
     * would show them an answer they had already replaced.
     */
    private void settle(String jobId, Models.Verdict sent, SyncState state, String reason) {
        boolean changed = false;
        synchronized (this) {
            FeedbackRecord current = get(jobId).orElse(null);
            if (current != null && current.verdict() == sent) {
                records = records.stream()
                        .map(record -> record.jobId().equals(jobId) ? record.in(state, reason) : record)
                        .toList();
                changed = true;
            }
        }
        if (changed) {
            persist();
            announce();
        }
    }

    private void put(FeedbackRecord record) {
        synchronized (this) {
            List<FeedbackRecord> next = new ArrayList<>();
            next.add(record);
            for (FeedbackRecord existing : records) {
                if (!existing.jobId().equals(record.jobId()) && next.size() < MAX_RECORDS) {
                    next.add(existing);
                }
            }
            records = List.copyOf(next);
        }
        persist();
        announce();
    }

    private void announce() {
        for (Runnable listener : listeners) {
            listener.run();
        }
    }

    private String now() {
        return clock.get().toString();
    }

    private static String message(Throwable cause) {
        String text = cause.getMessage();
        return text == null || text.isBlank() ? cause.getClass().getSimpleName() : text;
    }

    // --- the file ----------------------------------------------------------

    private List<FeedbackRecord> read() {
        if (file == null || !Files.isReadable(file)) {
            return List.of();
        }
        try {
            Saved saved = Json.read(Files.readString(file, StandardCharsets.UTF_8), Saved.class);
            return saved.records() == null ? List.of() : List.copyOf(saved.records());
        } catch (IOException | RuntimeException unreadable) {
            // A file half written by a previous run, or one this version does
            // not understand. Neither is a reason for the application not to
            // open, and the next verdict overwrites it.
            onError.accept(new IllegalStateException(
                    "could not read the verdicts kept at " + file + ": " + message(unreadable)));
            return List.of();
        }
    }

    private void persist() {
        if (file == null) {
            return;
        }
        try {
            Path parent = file.getParent();
            if (parent != null) {
                Files.createDirectories(parent);
            }
            Files.writeString(file, Json.write(new Saved(FORMAT_VERSION, all())),
                    StandardCharsets.UTF_8);
        } catch (IOException | RuntimeException cannotWrite) {
            // Out of space, or a directory that is not writable. The verdict
            // still shows for this session; it just will not outlive it.
            onError.accept(new IllegalStateException(
                    "could not keep the verdict on disk at " + file));
        }
    }
}
