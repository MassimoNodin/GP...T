use socket2::{Domain, Protocol, SockRef, Socket, Type};
use std::io;
use std::net::{SocketAddr, UdpSocket};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread::{self, JoinHandle};

use crate::consumer::ConsumerHub;
use crate::receiver::{Intake, Receiver, wake};
use crate::writer::{Sink, Writer};
use crate::{Clock, Consumer, KernelSampler, Metrics, PipelineEvent, ReceiverConfig, WriterConfig, WriterOutcome};

#[derive(Debug, Clone)]
pub struct PipelineConfig {
    pub listen: SocketAddr,
    /// Requested socket receive buffer; the OS may grant less (see `recv_buffer_bytes`).
    pub recv_buffer_bytes: usize,
    /// Capacity of each consumer's queue, in batches.
    pub consumer_queue_batches: usize,
    pub receiver: ReceiverConfig,
    pub writer: WriterConfig,
}

/// A running capture pipeline. Dropping it without [`stop`](Pipeline::stop)
/// leaves the threads running until the process exits.
pub struct Pipeline {
    shutdown: Arc<AtomicBool>,
    receiver: Option<JoinHandle<()>>,
    writer: Option<JoinHandle<io::Result<WriterOutcome>>>,
    local_addr: SocketAddr,
    metrics: Arc<Metrics>,
    recv_buffer_bytes: usize,
    kernel_drops_measurable: bool,
}

impl Pipeline {
    pub fn start(
        config: PipelineConfig,
        consumers: Vec<Box<dyn Consumer>>,
        on_event: Box<dyn FnMut(PipelineEvent<'_>) + Send>,
    ) -> io::Result<Self> {
        let clock = Clock::new();
        let sink = Sink::new(config.writer.sink.clone(), config.writer.metadata.clone(), clock.origin_unix_ns())?;
        let socket = bind(config.listen, config.recv_buffer_bytes)?;
        let local_addr = socket.local_addr()?;
        let recv_buffer_bytes = SockRef::from(&socket).recv_buffer_size().unwrap_or(0);
        let kernel = KernelSampler::for_socket(&socket);
        let kernel_drops_measurable = kernel.is_some();
        let reply = if config.writer.test_control { Some(socket.try_clone()?) } else { None };

        let metrics = Arc::new(Metrics::default());
        let shutdown = Arc::new(AtomicBool::new(false));
        let intake = Arc::new(Intake::new(&config.receiver));
        let hub = ConsumerHub::start(consumers, config.consumer_queue_batches, clock);

        let writer = Writer {
            config: config.writer,
            clock,
            metrics: Arc::clone(&metrics),
            hub,
            kernel,
            reply,
            sink,
            on_event,
            recv_buffer_bytes,
            intake: Arc::clone(&intake),
        };
        let writer = thread::Builder::new().name("f1-writer".into()).spawn(move || writer.run())?;

        let receiver = Receiver {
            socket,
            config: config.receiver,
            clock,
            metrics: Arc::clone(&metrics),
            intake,
            shutdown: Arc::clone(&shutdown),
        };
        let receiver = thread::Builder::new().name("f1-receiver".into()).spawn(move || receiver.run())?;

        Ok(Self {
            shutdown,
            receiver: Some(receiver),
            writer: Some(writer),
            local_addr,
            metrics,
            recv_buffer_bytes,
            kernel_drops_measurable,
        })
    }

    pub fn local_addr(&self) -> SocketAddr {
        self.local_addr
    }

    pub fn metrics(&self) -> &Arc<Metrics> {
        &self.metrics
    }

    /// Receive buffer size actually granted by the OS.
    pub fn recv_buffer_bytes(&self) -> usize {
        self.recv_buffer_bytes
    }

    pub fn kernel_drops_measurable(&self) -> bool {
        self.kernel_drops_measurable
    }

    /// Flag for a signal handler to set; the owner should then call [`stop`](Pipeline::stop).
    pub fn shutdown_flag(&self) -> Arc<AtomicBool> {
        Arc::clone(&self.shutdown)
    }

    /// True if the writer stopped on its own, which only happens on a fatal error.
    pub fn writer_finished(&self) -> bool {
        self.writer.as_ref().is_none_or(|handle| handle.is_finished())
    }

    /// Stops receiving, drains everything already received to disk and
    /// closes the open file with a footer.
    pub fn stop(mut self) -> io::Result<WriterOutcome> {
        self.shutdown.store(true, Ordering::Relaxed);
        wake(self.local_addr);
        if let Some(receiver) = self.receiver.take() {
            receiver.join().map_err(|_| io::Error::other("receiver thread panicked"))?;
        }
        let writer = self.writer.take().expect("stop is called once");
        writer.join().map_err(|_| io::Error::other("writer thread panicked"))?
    }
}

fn bind(listen: SocketAddr, recv_buffer_bytes: usize) -> io::Result<UdpSocket> {
    let socket = Socket::new(Domain::for_address(listen), Type::DGRAM, Some(Protocol::UDP))?;
    if recv_buffer_bytes > 0
        && let Err(err) = socket.set_recv_buffer_size(recv_buffer_bytes)
    {
        eprintln!("warning: could not set the receive buffer to {recv_buffer_bytes} bytes: {err}");
    }
    socket.bind(&listen.into())?;
    Ok(socket.into())
}
