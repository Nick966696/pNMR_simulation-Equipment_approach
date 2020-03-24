# This script simulates the FID signal of a pNMR probe.
#
# Author: René Reimann (2020)
#
# The ideas are based on DocDB #16856 and DocDB #11289
# https://gm2-docdb.fnal.gov/cgi-bin/private/ShowDocument?docid=16856
# https://gm2-docdb.fnal.gov/cgi-bin/private/ShowDocument?docid=11289

################################################################################
# Import first

import time
import numpy as np
from scipy import integrate
from numericalunits import µ0, NA, kB, mm, cm, m, s, ms, us, Hz, MHz
from numericalunits import T, K, J, g, mol, A, ohm, W, N, kg, V
import matplotlib.pyplot as plt

################################################################################
# Definition of constants used within the script
μₚ = 1.41060679736e-26*J/T  # proton magnetic moment
γₚ = 2.6752218744e8 *Hz/T # gyromagnetic ratio of proton

################################################################################

class Vector3D(object):
    def __init__(self, *args):
        self.val = np.array(args)

    def mag(self):
        return np.sqrt(np.sum(self.val**2))

    @property
    def x(self):
        return self.val[0]

    @x.setter
    def x(self, value):
        self.val[0] = value

    @property
    def y(self):
        return self.val[1]

    @y.setter
    def y(self, value):
        self.val[1] = value

    @property
    def z(self):
        return self.val[2]

    @z.setter
    def z(self, value):
        self.val[2] = value


class PermanentMagnet(object):
    def __init__(self, B0):
        self.B0 = B0

    def B_field(self, x=0, y=0, z=0):
        return Vector3D(0*T, 0*T, self.B0)

    def __call__(self, x=0, y=0, z=0):
        return self.B_field(x, y, z)


class Material(object):
    def __init__(self, name, formula=None, density=None, molar_mass=None, T1=None, T2=None):
        self.name = name
        self.formula = formula
        self.density = density
        self.molar_mass = molar_mass
        self.T1 = T1
        self.T2 = T2

    def __str__(self):
        info = []
        if self.formula is not None:
            info.append(formula)
        if self.density is not None:
            inof.append(density/(g/cm**3) + " g/cm^3")
        if self.molar_mass is not None:
            inof.append(molar_mass/(g/mol) + " g/mol")
        if self.T1 is not None:
            inof.append(T1/ms + " ms")
        if self.T2 is not None:
            inof.append(T2/ms + " ms")
        return name + "(" + ", ".join(info) + ")"

    @property
    def number_density(self):
        return NA * self.density / self.molar_mass


class Probe(object):
    class Cell(object):
        def __init__(self, r, phi, z):
            self.x =  r*np.sin(phi)
            self.y = r*np.cos(phi)
            self.z = z

    def __init__(self, length, diameter, material, temp, B_field, N_cells, seed):
        self.length = length
        self.radius = diameter / 2.
        self.V_cell = self.length * np.pi * self.radius**2

        self.material = material

        self.temp = temp
        self.B_field = B_field

        self.rng = np.random.RandomState(seed)
        self.N_cells = N_cells

        # dipoles are aligned with the external field at the beginning
        expon = μₚ * self.B_field(0*mm, 0*mm, 0*mm).mag() / (kB*temp)
        self.nuclear_polarization = (np.exp(expon) - np.exp(-expon))/(np.exp(expon) + np.exp(-expon))
        self.magnetization = μₚ * self.material.number_density * self.nuclear_polarization
        self.dipole_moment_mag = self.magnetization * self.V_cell/N_cells

        self.initialize_cells(self.N_cells)

    def initialize_cells(self, N_cells):
        rs = np.sqrt(self.rng.uniform(0,self.radius**2, size=N_cells))
        phis = self.rng.uniform(0, 2*np.pi, size=N_cells)
        zs = self.rng.uniform(-self.length/2., self.length/2., size=N_cells)
        self.cells = [Probe.Cell(r, phi, z) for r, phi, z in zip(rs, phis, zs)]

        for c in self.cells:
            c.B0 = self.B_field(c.x, c.y, c.z)

    def apply_rf_field(self, rf_field, time):
        # spin
        # aproximation

        for c in self.cells:
            c.B1 = rf_field(c.x, c.y, c.z)
        mu_x = lambda cell: np.sin(γₚ*cell.B1.mag()/2.*time)
        mu_y = lambda cell: np.sin(γₚ*cell.B1.mag()/2.*time)
        mu_z = lambda cell: np.cos(γₚ*cell.B1.mag()/2.*time)
        for c in self.cells:
            c.mu_x = mu_x(c)
            c.mu_y = mu_y(c)
            c.mu_z = mu_z(c)
            c.mu_T = np.sqrt(c.mu_x**2 + c.mu_y**2)

    def relax_B_dot_mu(self, t, mix_down=0*MHz):
        # a mix down_frequency can be propergated through and will effect the
        # individual cells, all operations before are linear
        mu_x = lambda cell : cell.mu_T*np.sin((γₚ*cell.B0.mag()-mix_down)*t)*np.exp(-t/self.material.T2)
        mu_y = lambda cell : cell.mu_T*np.cos((γₚ*cell.B0.mag()-mix_down)*t)*np.exp(-t/self.material.T2)
        mu_z = lambda cell : cell.mu_z

        return np.sum( [cell.B1.x * mu_x(cell) + cell.B1.y * mu_y(cell) + cell.B1.z * mu_z(cell) for cell in self.cells] )

class Coil(object):
    r"""A coil parametrized by number of turns, length, diameter and current.

    You can calculate the magnetic field cause by the coil at any point in space.
    """
    def __init__(self, turns, length, diameter, current):
        r""" Generates a coil objsect.

        Parameters:
        * turns: int
        * length: float
        * diameter: float
        * current: float
        """
        self.turns = turns
        self.length = length
        self.radius = diameter/2.
        self.current = current

    def B_field(self, x, y, z, **kwargs):
        r"""The magnetic field of the coil
        Assume Biot-Savart law
        vec(B)(vec(r)) = µ0 / 4π ∮ I dvec(L) × vec(r)' / |vec(r)'|³

        Approximations:
            - static, Biot-Savart law only holds for static current,
              in case of time-dependence use Jefimenko's equations.
              Jefimenko's equation:
              vec(B)(vec(r)) = µ0 / 4π ∫ ( J(r',tᵣ)/|r-r'|³ + 1/(|r-r'|² c)  ∂J(r',tᵣ)/∂t) × (r-r') d³r'
              with t_r = t-|r-r'|/c
              --> |r-r'|/c of order 0.1 ns
            - infinite small cables, if cables are extendet use the dV form
              vec(B)(vec(r))  = µ0 / 4π ∭_V  (vec(J) dV) × vec(r)' / |vec(r)'|³
            - closed loop
            - constant current, can factor out the I from integral
        """
        current = kwargs.pop("current", self.current)

        # coil path is implemented as perfect helix
        coil_path_parameter = np.linspace(0, 2*np.pi*self.turns, 1000)

        lx = lambda phi: self.radius * np.sin(phi)
        ly = lambda phi: self.radius * np.cos(phi)
        lz = lambda phi: self.length * phi / (2*np.pi*self.turns) - self.length/2.

        dlx = lambda phi: self.radius * np.cos(phi)
        dly = lambda phi: -self.radius * np.sin(phi)
        dlz = lambda phi: self.length / (2*np.pi*self.turns)

        dist = lambda phi, x, y, z: np.sqrt((lx(phi)-x)**2+(ly(phi)-y)**2+(lz(phi)-z)**2)

        integrand_x = lambda phi, x, y, z: ( dly(phi) * (z-lz(phi)) - dlz(phi) * (y-ly(phi)) ) / dist(phi, x, y, z)**3
        integrand_y = lambda phi, x, y, z: ( dlz(phi) * (x-lx(phi)) - dlx(phi) * (z-lz(phi)) ) / dist(phi, x, y, z)**3
        integrand_z = lambda phi, x, y, z: ( dlx(phi) * (y-ly(phi)) - dly(phi) * (x-lx(phi)) ) / dist(phi, x, y, z)**3

        B_x = lambda x, y, z : µ0/(4*np.pi) * current * integrate.quad(lambda phi: integrand_x(phi, x, y, z), 0, 2*np.pi*self.turns)[0]
        B_y = lambda x, y, z : µ0/(4*np.pi) * current * integrate.quad(lambda phi: integrand_y(phi, x, y, z), 0, 2*np.pi*self.turns)[0]
        B_z = lambda x, y, z : µ0/(4*np.pi) * current * integrate.quad(lambda phi: integrand_z(phi, x, y, z), 0, 2*np.pi*self.turns)[0]

        return Vector3D(B_x(x,y,z), B_y(x,y,z), B_z(x,y,z))

    def pickup_flux(self, probe, t, mix_down=0*MHz):
        # Φ(t) = Σ N B₂(r) * μ(t) / I
        # a mix down_frequency can be propergated through and will effect the
        # individual cells
        return self.turns * probe.relax_B_dot_mu(t, mix_down=mix_down) / self.current

################################################################################
# that about motion / diffusion within material
# what about other components of the probe
# what about spin-spin interactions

B0 = PermanentMagnet( 1.45*T )

# values from wolframalpha.com
petroleum_jelly = Material(name = "Petroleum Jelly",
                           formula = "C40H46N4O10",
                           density = 0.848*g/cm**3,
                           molar_mass = 742.8*g/mol,
                           T1 = 1*s,
                           T2 = 40*ms)

nmr_probe = Probe(length = 30.0*mm,
                  diameter = 1.5*mm,
                  material = petroleum_jelly,
                  temp = (273.15 + 26.85) * K,
                  B_field = B0,
                  N_cells = 1000,
                  seed = 12345)

impedance = 50 * ohm
guete = 0.60
pulse_power = 10*W
current = guete * np.sqrt(2*pulse_power/impedance)
print("I =", current/A, "A")

nmr_coil = Coil(turns=30,
               length=15.0*mm,
               diameter=4.6*mm,
               current=current)

if False:
    # make a plot for comparison
    cross_check = np.genfromtxt("./RF_coil_field_cross_check.txt", delimiter=", ")
    zs = np.linspace(-15*mm, 15*mm, 1000)
    plt.figure()
    plt.plot(cross_check[:,0], cross_check[:,1], label="Cross-Check from DocDB 16856, Slide 5\n$\O=2.3\,\mathrm{mm}$, $L=15\,\mathrm{mm}$, turns=30", color="orange")
    B_rf_z_0 = nmr_coil.B_field(0, 0, 0).z
    plt.plot(zs/mm, [nmr_coil.B_field(0, 0, z).z / B_rf_z_0 for z in zs], label="My calculation\n$\O=4.6\,\mathrm{mm}$, $L=15\,\mathrm{mm}$, turns=30", color="k", ls=":")
    plt.xlabel("z / mm")
    plt.ylabel("$B_z(0,0,z)\, /\, B_z(0,0,0)$")
    plt.legend(loc="lower right")
    plt.title("Magnetic field of the coil (static)")
    plt.tight_layout()
    plt.savefig("./plots/coil_field_distribution.pdf", bbox_inches="tight")

########################################################################################

# apply RF field
# B1 field strength of RF field
# for time of t_pi2 = pi/(2 gamma B1)

print("Brf(0,0,0)", nmr_coil.B_field(0*mm,0*mm,0*mm).mag()/T, "T")
t_90 = np.pi/(2*γₚ*nmr_coil.B_field(0*mm,0*mm,0*mm).mag()/2.)
print("t_90", t_90/us, "mus")
#t_90 = 10.0*us # sec
print("t_90", t_90/us, "mus")

nmr_probe.apply_rf_field(nmr_coil.B_field, t_90)

if False:
    zs = np.linspace(-15*mm, 15*mm, 1000)
    cross_check = np.genfromtxt("./mu_y_vs_z.txt", delimiter=" ")
    plt.figure()
    plt.plot(cross_check[:,0], cross_check[:,1], label="$\mu_y$, Cross-Check from DocDB 16856, Slide 7", color="orange")
    #scan = np.array([mu_x(0*mm,0*mm,z) for z in zs])
    plt.scatter([c.z/mm for c in nmr_probe.cells], [c.mu_x for c in nmr_probe.cells], label="$\mu_x$")
    plt.scatter([c.z/mm for c in nmr_probe.cells], [c.mu_y for c in nmr_probe.cells], label="$\mu_y$")
    plt.scatter([c.z/mm for c in nmr_probe.cells], [c.mu_z for c in nmr_probe.cells], label="$\mu_z$")
    plt.xlabel("z / mm")
    plt.ylabel("see legend")
    plt.legend()
    plt.tight_layout()
    plt.savefig("./plots/magnitization_after_pi2_pulse.pdf", bbox_inches="tight")
    plt.show()

# would have to solve Bloch Equations
#                             ( B_RF sin(omega*t) )
# dvec(M)/dt = gamma vec(M) x (         0         )
#                             (         B_z       )

####################################################################################


times = np.linspace(0*ms, 100*ms, 10000)
t0 = time.time()
flux = [nmr_coil.pickup_flux(nmr_probe, t, mix_down=61.74*MHz) for t in times]
print(time.time()-t0)

plt.figure()
plt.plot(times/ms, flux)
plt.show()

"""
Bz = B0
Brf = rnm_coil.B_field_mag(0*mm,0*mm,0*mm)
omega_NMR = 61.79*MHz    # circuit of the probe tuned for this value

def Bloch_equation_with_RF_field(t, M):
    dM_dt = γₚ*np.cross(M, [Brf*np.sin(omega_NMR*t), Brf*np.cos(omega_NMR*t), Bz])
    return dM_dt

rk_res = integrate.RK45(Bloch_equation_with_RF_field,
                        t0=0,
                        y0=[0.3,0.3,0.3],
                        t_bound=t_90,
                        max_step=t_90/100000)
history = []
while rk_res.status == "running":
    history.append([rk_res.t, rk_res.y])
    rk_res.step()

####################################################################################

def Bloch_equation_with_RF_field(M, t, γₚ, Bz, Brf, omega, T1=np.inf, T2=np.inf):
    # M is a vector of length 3 and is: M = [Mx, My, My].
    # Return dM_dt, that is a vector of length 3 as well.
    Mx, My, Mz = M
    M0 = 1
    #relaxation = np.array([-Mx/T2, -My/T2, -(Mz-M0)/T1])

    dM_dt = γₚ*np.cross(M, [Brf*np.sin(omega*t), Brf*np.cos(omega*t), Bz]) #+ relaxation
    return dM_dt

solution = integrate.odeint(Bloch_equation_with_RF_field,
                            y0=[0.3, 0.3, 0.3],
                            t=np.linspace(0., t_90, 100000),
                            args=(γₚ, B0, nmr_coil.B_field_mag(0*mm,0*mm,0*mm), omega_NMR ))


plt.figure()
plt.plot([h[1][0] for h in history], label="$M_x$ (RK45)")
plt.plot(solution[:,0], color="r", ls="--", label="$M_x$ (odeint)")
plt.plot(solution[:,1], color="b", label="$M_y$ (odeint)")
plt.plot(solution[:,2], color="g", label="$M_z$ (odeint)")
plt.xlabel("time / steps")
plt.ylabel("$M_i$")
plt.legend()
plt.show()

########################################################################################

# Add longitudinal relaxation (T1)

# integrate dM/dt with RK4
# dMx/dt = -γₚ(My*Bz-Mz*By) - Mx/T2
# dMy/dt = -γₚ(Mz*Bx-Mx*Bz) - My/T2
# dMz/dt = -γₚ(Mx*By-My*Bx) - (Mz-M0)/T1

# limit T2 --> infty: Mx/y(t) = exp(-t/T1) sin(omega t-phi0)

########################################################################################
"""
